"""Integration tests for the HITL scorer wiring (ADR-023, #215).

Drives a fresh upload through the in-memory pipeline up to
``NEEDS_REVIEW`` and asserts the side-effects this slice promises:

1. ``ValidationMetadata`` is upserted on the sidecar store with a
   non-trivial confidence score.
2. A ``confidence.scored`` audit event lands carrying the breakdown.
3. ``KW_HITL_DISABLE_SCORER=true`` makes both side-effects no-ops
   without breaking the FSM transition.
4. A scorer-internal failure is caught (fire-and-log) and the FSM
   transition still reaches ``NEEDS_REVIEW``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.dependencies import build_services
from app.main import create_app
from app.models.document import DocumentVersionStatus
from app.schemas.validation_metadata import ConfidenceScore, ValidationMetadata
from app.services.confidence_scorer import ALL_SIGNALS
from app.settings import Settings


def _land_in_needs_review(services) -> tuple[str, str]:
    version = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Hello world. This is a tiny test fixture.",
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    refreshed = services.documents.get_version(
        document_id=version.document_id, version_id=version.id
    )
    assert refreshed.status == DocumentVersionStatus.NEEDS_REVIEW
    return version.document_id, version.id


def test_scorer_fires_on_needs_review_transition():
    services = build_services()
    _, version_id = _land_in_needs_review(services)

    metadata = services.validation_metadata.get(version_id)
    assert metadata is not None
    assert metadata.version_id == version_id
    assert metadata.confidence_score is not None
    assert 0.0 <= metadata.confidence_score.overall <= 1.0
    # All five canonical signals must be present in the persisted row.
    assert set(metadata.confidence_score.signals.keys()) == set(ALL_SIGNALS)


def test_scorer_emits_confidence_scored_audit_event():
    """ADR-023 §5 + ADR-019 §4: every scoring pass lands a structured
    ``confidence.scored`` event so 'why was this version scored 0.7?'
    is a SQL query on the audit table.
    """
    services = build_services()
    create_app(services=services)
    _, version_id = _land_in_needs_review(services)

    rows = services.audit_events.query(event_name="confidence.scored")
    assert any(row.payload.get("version_id") == version_id for row in rows)
    matching = [r for r in rows if r.payload.get("version_id") == version_id]
    payload = matching[0].payload
    assert "overall" in payload
    assert "signals" in payload
    assert "weights" in payload
    assert payload["computed_by_version"] == "v1"


def test_scorer_disabled_via_env_skips_side_effects(monkeypatch):
    """ADR-023 §5: ``KW_HITL_DISABLE_SCORER=true`` is the demo escape hatch.
    The transition keeps working, no metadata row is written, no
    audit event is emitted.
    """
    monkeypatch.setenv("KW_HITL_DISABLE_SCORER", "true")
    services = build_services()
    assert services.confidence_scorer is None
    create_app(services=services)
    _, version_id = _land_in_needs_review(services)

    assert services.validation_metadata.get(version_id) is None
    rows = services.audit_events.query(event_name="confidence.scored")
    assert not any(row.payload.get("version_id") == version_id for row in rows)


def test_scorer_failure_does_not_roll_back_transition():
    """ADR-012 fire-and-log discipline applies: a scorer hiccup must
    NOT roll back the FSM transition. The catalog stays the source
    of truth; the metadata catches up via re-scoring on the router
    read path.
    """
    services = build_services()

    class _FlakyScorer:
        SCORER_VERSION = "v1"

        @property
        def weights(self) -> dict[str, float]:
            return dict.fromkeys(ALL_SIGNALS, 0.2)

        def score(self, **_kwargs):
            raise RuntimeError("simulated scoring outage")

    services.semantic_outputs.confidence_scorer = _FlakyScorer()  # type: ignore[assignment]

    # Transition must still complete despite the scorer raising.
    version = services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Tiny.",
    )
    services.extraction_jobs.extract(document_id=version.document_id, version_id=version.id)
    services.semantic_outputs.generate(document_id=version.document_id, version_id=version.id)
    refreshed = services.documents.get_version(
        document_id=version.document_id, version_id=version.id
    )
    assert refreshed.status == DocumentVersionStatus.NEEDS_REVIEW
    # No metadata persisted because the scorer raised before upsert.
    assert services.validation_metadata.get(version.id) is None


def test_settings_hitl_weights_default_equal():
    """Sanity-check the env-derived weights map matches ADR-023 §2's defaults."""
    settings = Settings()
    weights = settings.hitl_weights
    assert set(weights.keys()) == set(ALL_SIGNALS)
    assert all(value == pytest.approx(0.2) for value in weights.values())


def test_settings_hitl_threshold_default():
    settings = Settings()
    assert settings.hitl_auto_validate_threshold == pytest.approx(0.85)


def test_catalog_norm_sample_provider_walks_catalog():
    """The persistent wiring's :class:`_CatalogNormSampleProvider`
    walks the catalog's semantic documents to seed corpus norms.
    Exercising it here keeps the coverage on the production path
    even though the in-memory wiring uses an empty norms store
    out of the box.
    """
    from app.dependencies import _CatalogNormSampleProvider

    services = build_services()
    _land_in_needs_review(services)

    provider = _CatalogNormSampleProvider(documents=services.documents)
    samples = provider.section_length_samples(content_type="text/plain", topic_cluster="")
    assert samples  # at least the one section we just wrote
    asset_samples = provider.asset_count_samples(content_type="text/plain", topic_cluster="")
    assert asset_samples == [0]


def test_catalog_norm_sample_provider_skips_versions_without_semantic():
    """A version that has no semantic doc yet must NOT raise — the
    provider silently skips it. Exercises the ``except KeyError`` branch.
    """
    from app.dependencies import _CatalogNormSampleProvider

    services = build_services()
    # Upload but do NOT extract / generate, so no semantic doc exists.
    services.documents.upload(
        filename="dangling.txt",
        content_type="text/plain",
        content=b"orphan",
    )
    provider = _CatalogNormSampleProvider(documents=services.documents)
    # Must not raise; samples list is empty for this content type.
    assert provider.section_length_samples(content_type="text/plain", topic_cluster="") == []
    assert provider.asset_count_samples(content_type="text/plain", topic_cluster="") == []


def test_validation_metadata_round_trip_via_store():
    """End-to-end shape check: a full ``ValidationMetadata`` (score +
    routing decision + actor) survives a write/read cycle through the
    container's metadata store. Catches a regression where the
    next-slice router writes the routing fields and the audit query
    fails to read them back."""
    services = build_services()
    score = ConfidenceScore(
        overall=0.91,
        signals=dict.fromkeys(ALL_SIGNALS, 0.91),
        weights=dict.fromkeys(ALL_SIGNALS, 0.2),
        ocr_override_active=False,
        computed_at=datetime(2026, 5, 5, 12, 0, tzinfo=UTC),
        computed_by_version="v1",
    )
    metadata = ValidationMetadata(
        version_id="ver-future",
        confidence_score=score,
        routing_decision="auto",
        validation_method="auto",
    )
    services.validation_metadata.upsert(metadata)
    fetched = services.validation_metadata.get("ver-future")
    assert fetched is not None
    assert fetched.routing_decision == "auto"
    assert fetched.confidence_score is not None
    assert fetched.confidence_score.overall == pytest.approx(0.91)
