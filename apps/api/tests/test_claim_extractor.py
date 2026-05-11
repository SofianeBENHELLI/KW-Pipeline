"""Tests for ``ClaimExtractor`` (#392, ADR-031).

The default ``pytest`` invocation must never reach the network — every
test in this module enqueues recorded LLM responses on a stub client
and asserts on the extractor's behaviour, not the SDK's. These tests
also exercise the projector hook (``set_claim_extractor`` /
``set_claim_store``) so we cover the fire-and-log boundary the
operator workflow depends on.

Coverage:

* Whitespace / empty sections are skipped without an LLM call.
* Valid JSON responses parse into :class:`Claim` instances.
* Per-claim parse errors are skipped; valid claims keep flowing.
* Claims without ``provenance_chunk_ids`` are dropped at the policy
  gate (default-deny on provenance).
* Per-section token guard: when the cap is set and a section's text
  exceeds it, the section is skipped (no LLM call).
* ``_maybe_build_claim_extractor`` returns ``None`` without an LLM
  and a real extractor when the LLM is wired.
* Projector hook fires only when both extractor + store are set.
* Re-projection deletes prior claims for the same ``version_id``.
* Extractor failures are swallowed by the projector hook (fire-and-log).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.dependencies import _maybe_build_claim_extractor
from app.models.document import DocumentVersionStatus
from app.schemas.claim import Claim
from app.schemas.document import Document, DocumentVersion
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.claim_extractor import ClaimExtractor
from app.services.claim_store import InMemoryClaimStore
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.llm_client import LLMClient
from app.services.knowledge.projector import KnowledgeProjector

# ─── Test doubles ────────────────────────────────────────────────────


class _StubLLM:
    """In-process ``LLMClient`` that returns canned responses per call.

    Mirrors :class:`FakeLLMClient` but stays local to this test module
    so the assertions over ``calls`` don't drag in entity-extractor
    fixtures. Implements both Protocol methods so it satisfies the
    runtime check.
    """

    name: str = "stub"

    def __init__(self) -> None:
        self._responses: list[tuple[dict[str, Any], dict[str, int]]] = []
        self.calls: list[dict[str, Any]] = []
        self._raise_exc: Exception | None = None

    def enqueue(
        self,
        parsed_tool_input: dict[str, Any],
        token_usage: dict[str, int] | None = None,
    ) -> None:
        self._responses.append((parsed_tool_input, token_usage or {}))

    def fail_with(self, exc: Exception) -> None:
        """Configure the next call(s) to raise ``exc``."""
        self._raise_exc = exc

    def complete_with_tool(
        self,
        *,
        system: str,
        user: str,
        tool_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        self.calls.append({"system": system, "user": user, "tool_schema": tool_schema})
        if self._raise_exc is not None:
            raise self._raise_exc
        if not self._responses:
            raise RuntimeError(
                "_StubLLM: no recorded responses left. Call enqueue(...) once "
                "per expected LLM call."
            )
        return self._responses.pop(0)

    def complete_text(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        # Not used by ClaimExtractor; included for Protocol parity.
        raise NotImplementedError


def _make_version(*, document_id: str = "doc-1", version_id: str = "ver-1") -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        filename="policy.txt",
        content_type="text/plain",
        file_size=42,
        sha256="0" * 64,
        storage_uri="file://fake",
        status=DocumentVersionStatus.VALIDATED,
    )


def _make_document(version: DocumentVersion) -> Document:
    return Document(
        id=version.document_id,
        original_filename=version.filename,
        latest_version_id=version.id,
        versions=[version],
    )


def _make_semantic(
    *,
    version: DocumentVersion,
    sections: list[SemanticSection],
) -> SemanticDocument:
    return SemanticDocument(
        id=f"sem-{version.id}",
        document_version_id=version.id,
        document_profile=DocumentProfile(title="Test Doc"),
        sections=sections,
        validation_status="validated",
        markdown="# Test\n",
        created_at=datetime(2026, 5, 11, tzinfo=UTC),
    )


# ─── Extractor unit tests ────────────────────────────────────────────


def test_stub_llm_satisfies_protocol() -> None:
    stub = _StubLLM()
    assert isinstance(stub, LLMClient)


def test_extract_returns_parsed_claims() -> None:
    stub = _StubLLM()
    stub.enqueue(
        {
            "claims": [
                {
                    "subject_entity_id": "entity-abc123",
                    "predicate": "is_a",
                    "object_value": "policy",
                    "object_entity_id": None,
                    "confidence": 0.92,
                    "provenance_chunk_ids": ["s1"],
                }
            ]
        },
        {"input_tokens": 100, "output_tokens": 25},
    )
    extractor = ClaimExtractor(llm=stub, model="claude-sonnet-4-5")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(
                id="s1",
                heading="Compliance",
                text="ISO 9001 is a quality management policy.",
            )
        ],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(claims) == 1
    claim = claims[0]
    assert claim.subject_entity_id == "entity-abc123"
    assert claim.predicate == "is_a"
    assert claim.object_value == "policy"
    assert claim.object_entity_id is None
    assert claim.provenance_chunk_ids == ["s1"]
    assert claim.id == f"claim-{version.id}-0"
    assert claim.document_id == version.document_id
    assert claim.version_id == version.id
    # The stub was called exactly once for the single section.
    assert len(stub.calls) == 1


def test_extract_skips_empty_and_whitespace_sections() -> None:
    stub = _StubLLM()
    # Only one queued response — the second section (whitespace) must
    # NOT trigger an LLM call. If it did, _StubLLM would raise on the
    # second pop attempt and the test would fail.
    stub.enqueue(
        {
            "claims": [
                {
                    "subject_entity_id": "entity-x",
                    "predicate": "p",
                    "object_value": "y",
                    "object_entity_id": None,
                    "confidence": 0.8,
                    "provenance_chunk_ids": ["s1"],
                }
            ]
        }
    )
    extractor = ClaimExtractor(llm=stub, model="m")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="real content"),
            SemanticSection(id="s2", heading="B", text="   \n\t   "),
            SemanticSection(id="s3", heading="C", text=""),
        ],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(claims) == 1
    # Exactly one LLM call — the empty / whitespace sections were
    # skipped without invoking the model.
    assert len(stub.calls) == 1


def test_extract_skips_per_claim_parse_errors_keeps_valid_ones() -> None:
    """One malformed claim must not lose the rest of the batch."""
    stub = _StubLLM()
    stub.enqueue(
        {
            "claims": [
                {
                    "subject_entity_id": "entity-1",
                    "predicate": "p",
                    # Both object_value AND object_entity_id set → schema
                    # XOR rejects this; the extractor catches and skips.
                    "object_value": "v",
                    "object_entity_id": "entity-2",
                    "confidence": 0.5,
                    "provenance_chunk_ids": ["s1"],
                },
                {
                    "subject_entity_id": "entity-3",
                    "predicate": "q",
                    "object_value": "w",
                    "object_entity_id": None,
                    "confidence": 0.9,
                    "provenance_chunk_ids": ["s1"],
                },
            ]
        }
    )
    extractor = ClaimExtractor(llm=stub, model="m")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="text")],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(claims) == 1
    assert claims[0].subject_entity_id == "entity-3"


def test_extract_drops_claims_without_provenance() -> None:
    """Default-deny on provenance: no chunk ids → claim is rejected."""
    stub = _StubLLM()
    stub.enqueue(
        {
            "claims": [
                {
                    "subject_entity_id": "entity-1",
                    "predicate": "p",
                    "object_value": "v",
                    "object_entity_id": None,
                    "confidence": 0.5,
                    "provenance_chunk_ids": [],
                },
                {
                    "subject_entity_id": "entity-2",
                    "predicate": "q",
                    "object_value": "w",
                    "object_entity_id": None,
                    "confidence": 0.9,
                    "provenance_chunk_ids": ["s1"],
                },
            ]
        }
    )
    extractor = ClaimExtractor(llm=stub, model="m")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="text")],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(claims) == 1
    assert claims[0].subject_entity_id == "entity-2"
    assert claims[0].provenance_chunk_ids == ["s1"]


def test_extract_token_guard_skips_long_sections() -> None:
    stub = _StubLLM()
    # No queued responses — an LLM call would raise. The token guard
    # must skip the section before reaching the call.
    extractor = ClaimExtractor(llm=stub, model="m", max_input_tokens=10)

    version = _make_version()
    long_text = "x" * 100
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text=long_text),
        ],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert claims == []
    assert stub.calls == []


def test_extract_token_guard_disabled_by_zero() -> None:
    """``max_input_tokens=0`` (the default) means no cap applies."""
    stub = _StubLLM()
    stub.enqueue(
        {
            "claims": [
                {
                    "subject_entity_id": "entity-1",
                    "predicate": "p",
                    "object_value": "v",
                    "object_entity_id": None,
                    "confidence": 0.5,
                    "provenance_chunk_ids": ["s1"],
                }
            ]
        }
    )
    extractor = ClaimExtractor(llm=stub, model="m", max_input_tokens=0)

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="x" * 10000)],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(claims) == 1


def test_extract_negative_token_cap_is_rejected() -> None:
    stub = _StubLLM()
    with pytest.raises(ValueError):
        ClaimExtractor(llm=stub, model="m", max_input_tokens=-1)


def test_extract_section_id_always_in_provenance() -> None:
    """The extractor adds the section id to provenance even when the
    LLM emits a claim that doesn't list it (defence in depth so a
    misbehaving model still grounds at the section level)."""
    stub = _StubLLM()
    stub.enqueue(
        {
            "claims": [
                {
                    "subject_entity_id": "entity-1",
                    "predicate": "p",
                    "object_value": "v",
                    "object_entity_id": None,
                    "confidence": 0.5,
                    # Cite a different chunk id; the section id must
                    # still be added.
                    "provenance_chunk_ids": ["other-chunk"],
                }
            ]
        }
    )
    extractor = ClaimExtractor(llm=stub, model="m")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="text")],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(claims) == 1
    assert "s1" in claims[0].provenance_chunk_ids
    # And we don't duplicate when the LLM already includes the id.
    assert claims[0].provenance_chunk_ids.count("s1") == 1


def test_extract_swallows_per_section_llm_failure() -> None:
    """A per-section LLM exception is logged and skipped — the rest
    of the document still extracts."""
    stub = _StubLLM()
    stub.fail_with(RuntimeError("upstream blew up"))
    extractor = ClaimExtractor(llm=stub, model="m")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="text")],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert claims == []


def test_extract_rejects_mismatched_version() -> None:
    extractor = ClaimExtractor(llm=_StubLLM(), model="m")
    version = _make_version(version_id="ver-1")
    semantic = SemanticDocument(
        id="sem-x",
        document_version_id="ver-OTHER",
        document_profile=DocumentProfile(title="t"),
        sections=[],
    )
    with pytest.raises(ValueError, match="not for version"):
        extractor.extract(semantic, document=_make_document(version), version=version)


# ─── _maybe_build_claim_extractor wiring tests ───────────────────────


def test_maybe_build_claim_extractor_returns_none_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KW_KNOWLEDGE_LAYER_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("KW_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("KW_ANTHROPIC_API_KEY", raising=False)
    assert _maybe_build_claim_extractor() is None


def test_maybe_build_claim_extractor_built_when_llm_supplied() -> None:
    stub = _StubLLM()
    extractor = _maybe_build_claim_extractor(llm=stub, llm_model="claude-sonnet-4-5")
    assert isinstance(extractor, ClaimExtractor)


# ─── Projector hook integration tests ────────────────────────────────


def _claim_response_for_section(section_id: str, *, subject: str = "entity-x") -> dict[str, Any]:
    return {
        "claims": [
            {
                "subject_entity_id": subject,
                "predicate": "is_a",
                "object_value": "thing",
                "object_entity_id": None,
                "confidence": 0.9,
                "provenance_chunk_ids": [section_id],
            }
        ]
    }


def test_projector_hook_writes_claims_when_both_wired() -> None:
    """Both extractor + store wired → projection emits Claims into the store."""
    stub = _StubLLM()
    stub.enqueue(_claim_response_for_section("s1", subject="entity-a"))
    extractor = ClaimExtractor(llm=stub, model="m")
    store = InMemoryClaimStore()

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_claim_extractor(extractor)
    projector.set_claim_store(store)

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    projector.project(document=_make_document(version), version=version, semantic=semantic)

    items, _ = store.list_for_subject("entity-a")
    assert len(items) == 1
    assert items[0].subject_entity_id == "entity-a"
    assert items[0].version_id == version.id


def test_projector_hook_skipped_when_store_missing() -> None:
    """Both-or-nothing gate: extractor wired, store None → no LLM call."""
    stub = _StubLLM()
    # No enqueued response — if the hook fires the LLM call would raise.
    extractor = ClaimExtractor(llm=stub, model="m")

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_claim_extractor(extractor)
    # Intentionally do NOT call set_claim_store.

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    # Should not raise.
    projector.project(document=_make_document(version), version=version, semantic=semantic)
    assert stub.calls == []


def test_projector_with_no_setters_does_not_extract() -> None:
    """Regression guard: a projector built without the new setters
    must behave identically to the pre-#392 path."""
    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    # Should not raise — no claim store to write into, no extractor
    # wired.
    projector.project(document=_make_document(version), version=version, semantic=semantic)


def test_projector_hook_replaces_claims_on_re_projection() -> None:
    """Re-projecting the same version drops the prior batch via
    ``delete_for_version`` before saving the new one."""
    stub = _StubLLM()
    # Two LLM responses for two projection passes.
    stub.enqueue(_claim_response_for_section("s1", subject="entity-old"))
    stub.enqueue(_claim_response_for_section("s1", subject="entity-new"))
    extractor = ClaimExtractor(llm=stub, model="m")
    store = InMemoryClaimStore()

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_claim_extractor(extractor)
    projector.set_claim_store(store)

    version = _make_version()
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    projector.project(document=document, version=version, semantic=semantic)
    projector.project(document=document, version=version, semantic=semantic)

    # The "old" subject should have no claims after re-projection.
    old_items, _ = store.list_for_subject("entity-old")
    assert old_items == []

    # The "new" subject should have exactly the latest batch.
    new_items, _ = store.list_for_subject("entity-new")
    assert len(new_items) == 1
    assert new_items[0].version_id == version.id


def test_projector_hook_swallows_extractor_failure() -> None:
    """A Claim extractor exception must not roll back the projection.

    Same fire-and-log boundary as the existing embedding / cache
    hooks — the catalog stays the source of truth.
    """
    stub = _StubLLM()
    stub.fail_with(RuntimeError("boom"))
    extractor = ClaimExtractor(llm=stub, model="m")
    store = InMemoryClaimStore()

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_claim_extractor(extractor)
    projector.set_claim_store(store)

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    # Must not raise.
    projector.project(document=_make_document(version), version=version, semantic=semantic)


def test_projector_hook_skips_save_when_no_claims_extracted() -> None:
    """Empty extraction result still calls ``delete_for_version`` so a
    re-projection that now yields zero claims removes the prior batch.

    This protects against a stale claim from a previous batch lingering
    when the model decides the new version has no atomic claims."""
    stub = _StubLLM()
    # First call returns a claim; second returns empty.
    stub.enqueue(_claim_response_for_section("s1", subject="entity-keep"))
    stub.enqueue({"claims": []})
    extractor = ClaimExtractor(llm=stub, model="m")
    store = InMemoryClaimStore()

    projector = KnowledgeProjector(graph_store=InMemoryGraphStore())
    projector.set_claim_extractor(extractor)
    projector.set_claim_store(store)

    version = _make_version()
    document = _make_document(version)
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    projector.project(document=document, version=version, semantic=semantic)
    items, _ = store.list_for_subject("entity-keep")
    assert len(items) == 1

    # Re-project with empty result → prior batch is gone.
    projector.project(document=document, version=version, semantic=semantic)
    items_after, _ = store.list_for_subject("entity-keep")
    assert items_after == []


def test_extracted_claims_are_persistable_through_save_claims() -> None:
    """End-to-end: an extracted Claim flows through ``save_claims``
    without the store complaining about the sentinel ``extracted_at``.

    Guards against a regression where a future store impl tightens the
    ``extracted_at`` contract and the sentinel-then-overwrite flow stops
    working.
    """
    stub = _StubLLM()
    stub.enqueue(_claim_response_for_section("s1", subject="entity-abc"))
    extractor = ClaimExtractor(llm=stub, model="m")
    store = InMemoryClaimStore()

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="alpha")],
    )

    claims = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )
    store.save_claims(claims)

    items, _ = store.list_for_subject("entity-abc")
    assert len(items) == 1
    persisted: Claim = items[0]
    # The store stamps the canonical ``extracted_at`` — the sentinel
    # epoch value the extractor minted is gone.
    assert persisted.extracted_at.year >= 2026
