"""Tests for the deterministic knowledge export package (closes #23).

The contract is documented in
``docs/architecture/knowledge_export_contract.md``. The tests here
assert the three properties consumers depend on:

1. **Deterministic chunk/asset IDs** — re-exporting the same version
   produces identical IDs even when whitespace shifts inside the
   sections.
2. **Stable package_sha256** — sorting at the top level keeps the
   hash invariant under reorderings of the source semantic document.
3. **Validation status preservation** — the version-level
   ``validation_status`` and the per-asset ``review_status`` ride
   through to the package without translation.

All tests run against ``KnowledgeExporter._build`` so we don't have
to spin up the full service graph; the logic that matters
(deterministic IDs, sha256, manifest assembly) is fully exercised.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from app.schemas.document import DocumentVersion
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticAsset,
    SemanticDocument,
    SemanticSection,
)
from app.services.knowledge_exporter import (
    KnowledgeExporter,
    _normalize_text,
    _package_sha256,
)


def _version(
    *,
    version_id: str = "ver-1",
    document_id: str = "doc-1",
    sha256_hex: str = "a" * 64,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=3,
        filename="supplier-onboarding.pdf",
        content_type="application/pdf",
        file_size=1024,
        sha256=sha256_hex,
        storage_uri="memory://x",
        status="VALIDATED",
    )


def _semantic(
    *,
    document_version_id: str = "ver-1",
    sections: list[SemanticSection] | None = None,
    assets: list[SemanticAsset] | None = None,
    validation_status: str = "validated",
) -> SemanticDocument:
    return SemanticDocument(
        id="sem-1",
        document_version_id=document_version_id,
        document_profile=DocumentProfile(title="Test", document_type="policy"),
        sections=sections or [],
        assets=assets or [],
        validation_status=validation_status,  # type: ignore[arg-type]
        markdown="# Title\n\nbody.",
        created_at=datetime(2026, 5, 7, tzinfo=UTC),
    )


def _exporter() -> KnowledgeExporter:
    """Build an exporter with stub stores — the service-level wiring is
    out-of-scope for these tests; we drive ``_build`` directly.
    """
    return KnowledgeExporter(documents=None, semantic_outputs=None)  # type: ignore[arg-type]


# ── Normalization ─────────────────────────────────────────────────────


class TestNormalization:
    def test_collapses_internal_whitespace_runs(self) -> None:
        assert _normalize_text("a  b\tc\n  d") == "a b c d"

    def test_strips_leading_and_trailing_whitespace(self) -> None:
        assert _normalize_text("\n  hello world  \n") == "hello world"

    def test_nfkc_normalizes_compatibility_characters(self) -> None:
        # ``ﬁ`` (U+FB01 LATIN SMALL LIGATURE FI) → ``fi`` under NFKC.
        # Without normalization, a parser that decomposes ligatures
        # would break the chunk_id cache key.
        assert _normalize_text("eﬃcient") == "efficient"


# ── Deterministic chunk IDs ───────────────────────────────────────────


class TestDeterministicChunkIds:
    def test_same_input_yields_same_chunk_id(self) -> None:
        section = SemanticSection(id="s-1", heading="H", text="Hello world.")
        sem = _semantic(sections=[section])
        v = _version()
        first = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem)
        second = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem)
        assert first.chunks[0].chunk_id == second.chunks[0].chunk_id

    def test_whitespace_only_changes_yield_same_chunk_id(self) -> None:
        # The parser sometimes re-wraps text on a re-extraction; the
        # content-addressed id must survive that.
        sem_a = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="foo bar baz")],
        )
        sem_b = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="foo  bar\nbaz")],
        )
        v = _version()
        a = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_a)
        b = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_b)
        assert a.chunks[0].chunk_id == b.chunks[0].chunk_id
        assert a.chunks[0].content_sha256 == b.chunks[0].content_sha256

    def test_text_change_yields_different_chunk_id(self) -> None:
        sem_a = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="hello")],
        )
        sem_b = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="goodbye")],
        )
        v = _version()
        a = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_a)
        b = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_b)
        assert a.chunks[0].chunk_id != b.chunks[0].chunk_id

    def test_chunk_id_salted_with_document_and_version(self) -> None:
        # Two different versions emit different chunk_ids for the same
        # text — preserves the boilerplate-no-collide invariant.
        section = SemanticSection(id="s-1", heading="H", text="Identical text.")
        sem_v1 = _semantic(document_version_id="ver-1", sections=[section])
        sem_v2 = _semantic(document_version_id="ver-2", sections=[section])
        v1 = _version(version_id="ver-1")
        v2 = _version(version_id="ver-2")
        a = _exporter()._build(document_or_filename="f.pdf", version=v1, semantic=sem_v1)
        b = _exporter()._build(document_or_filename="f.pdf", version=v2, semantic=sem_v2)
        assert a.chunks[0].chunk_id != b.chunks[0].chunk_id

    def test_chunk_id_format(self) -> None:
        sem = _semantic(sections=[SemanticSection(id="s-1", heading="H", text="t")])
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        chunk_id = out.chunks[0].chunk_id
        assert chunk_id.startswith("chunk_")
        # 16 lowercase hex digits after the prefix.
        assert len(chunk_id) == len("chunk_") + 16
        assert all(c in "0123456789abcdef" for c in chunk_id[len("chunk_") :])

    def test_repeated_identical_sections_get_distinct_ids(self) -> None:
        # Two sections in one version with identical content (e.g.
        # boilerplate "Page 1 of N" headers): without disambiguation
        # they would collide and consumers using the chunk_id as an
        # upsert key would lose one of them.
        boiler = SemanticSection(id="s-1", heading="H", text="Page footer boilerplate.")
        repeat = SemanticSection(id="s-2", heading="H", text="Page footer boilerplate.")
        sem = _semantic(sections=[boiler, repeat])
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        ids = [c.chunk_id for c in out.chunks]
        assert len(ids) == 2
        assert len(set(ids)) == 2, "duplicate-content sections must not collide"
        # ``content_sha256`` stays equal because it hashes the
        # normalized text, not the disambiguator — consumers can still
        # spot identical-content rows via that field.
        assert out.chunks[0].content_sha256 == out.chunks[1].content_sha256

    def test_first_occurrence_is_pure_content_addressed(self) -> None:
        # The cache-stability promise (re-extraction with same content
        # yields same id) must hold for non-duplicated rows AND for the
        # first occurrence of a duplicated row. Otherwise adding a new
        # duplicate would invalidate the original cache entry.
        sem_alone = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="boilerplate")],
        )
        sem_with_dup = _semantic(
            sections=[
                SemanticSection(id="s-1", heading="H", text="boilerplate"),
                SemanticSection(id="s-2", heading="H", text="boilerplate"),
            ],
        )
        out_alone = _exporter()._build(
            document_or_filename="f.pdf", version=_version(), semantic=sem_alone
        )
        out_with_dup = _exporter()._build(
            document_or_filename="f.pdf", version=_version(), semantic=sem_with_dup
        )
        assert out_alone.chunks[0].chunk_id == out_with_dup.chunks[0].chunk_id

    def test_disambiguation_is_stable_across_re_exports(self) -> None:
        sections = [
            SemanticSection(id="s-1", heading="H", text="repeat"),
            SemanticSection(id="s-2", heading="H", text="repeat"),
            SemanticSection(id="s-3", heading="H", text="repeat"),
        ]
        sem = _semantic(sections=sections)
        first = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        second = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]
        # All three ids are distinct.
        assert len({c.chunk_id for c in first.chunks}) == 3


# ── Deterministic asset IDs ───────────────────────────────────────────


class TestDeterministicAssetIds:
    def test_assets_of_different_types_share_text_but_distinct_ids(self) -> None:
        sem = _semantic(
            assets=[
                SemanticAsset(
                    type="policy_rule",
                    text="Rule X applies.",
                    confidence=0.9,
                ),
                SemanticAsset(
                    type="risk",
                    text="Rule X applies.",
                    confidence=0.5,
                ),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        ids = {a.asset_id for a in out.assets}
        assert len(ids) == 2

    def test_asset_id_format(self) -> None:
        sem = _semantic(
            assets=[
                SemanticAsset(type="policy_rule", text="t", confidence=0.5),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        aid = out.assets[0].asset_id
        assert aid.startswith("asset_")
        assert len(aid) == len("asset_") + 16

    def test_repeated_same_type_assets_get_distinct_ids(self) -> None:
        # Two ``policy_rule`` assets with identical text in one version
        # (the LLM emitted the same rule twice — possible when the
        # prompt covers two paragraphs that both restate the rule).
        sem = _semantic(
            assets=[
                SemanticAsset(type="policy_rule", text="No PII in logs.", confidence=0.9),
                SemanticAsset(type="policy_rule", text="No PII in logs.", confidence=0.5),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        ids = [a.asset_id for a in out.assets]
        assert len(ids) == 2
        assert len(set(ids)) == 2, "duplicate-content same-type assets must not collide"
        assert out.assets[0].content_sha256 == out.assets[1].content_sha256

    def test_first_asset_occurrence_is_pure_content_addressed(self) -> None:
        sem_alone = _semantic(
            assets=[SemanticAsset(type="policy_rule", text="x", confidence=0.9)],
        )
        sem_with_dup = _semantic(
            assets=[
                SemanticAsset(type="policy_rule", text="x", confidence=0.9),
                SemanticAsset(type="policy_rule", text="x", confidence=0.4),
            ],
        )
        out_alone = _exporter()._build(
            document_or_filename="f.pdf", version=_version(), semantic=sem_alone
        )
        out_with_dup = _exporter()._build(
            document_or_filename="f.pdf", version=_version(), semantic=sem_with_dup
        )
        assert out_alone.assets[0].asset_id == out_with_dup.assets[0].asset_id


# ── Manifest completeness ─────────────────────────────────────────────


class TestManifestCompleteness:
    def test_manifest_carries_every_required_metadata_field(self) -> None:
        sem = _semantic(
            sections=[
                SemanticSection(id="s-1", heading="H", text="t"),
                SemanticSection(id="s-2", heading="H2", text="t2"),
            ],
            assets=[
                SemanticAsset(type="policy_rule", text="rule.", confidence=0.9),
            ],
        )
        version = _version(sha256_hex="b" * 64)
        out = _exporter()._build(
            document_or_filename="supplier-onboarding.pdf",
            version=version,
            semantic=sem,
        )
        m = out.manifest
        assert m.schema_version == "v0.1"
        assert m.document_id == "doc-1"
        assert m.document_version_id == "ver-1"
        assert m.document_version_number == 3
        assert m.original_filename == "supplier-onboarding.pdf"
        assert m.version_filename == "supplier-onboarding.pdf"
        assert m.document_sha256 == "b" * 64
        assert m.content_type == "application/pdf"
        assert m.semantic_schema_version == "v0.1"
        assert m.validation_status == "validated"
        assert m.document_type == "policy"
        assert m.chunk_count == 2
        assert m.asset_count == 1
        assert len(m.package_sha256) == 64

    def test_markdown_blob_rides_through_to_the_package(self) -> None:
        sem = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="t")],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        assert out.markdown == "# Title\n\nbody."


# ── Validation-status labelling ───────────────────────────────────────


class TestValidationStatusLabelling:
    @pytest.mark.parametrize("status", ["needs_review", "validated", "rejected"])
    def test_version_level_validation_status_propagates_to_chunks(self, status: str) -> None:
        sem = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="t")],
            validation_status=status,
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        assert out.manifest.validation_status == status
        assert all(c.validation_status == status for c in out.chunks)

    def test_per_asset_review_status_preserved_unchanged(self) -> None:
        sem = _semantic(
            assets=[
                SemanticAsset(
                    type="policy_rule",
                    text="needs review",
                    confidence=0.5,
                    review_status="needs_review",
                ),
                SemanticAsset(
                    type="policy_rule",
                    text="source backed",
                    confidence=0.9,
                    review_status="source_backed",
                    source_reference_ids=["src-p3-para0"],
                ),
                SemanticAsset(
                    type="policy_rule",
                    text="validated",
                    confidence=0.95,
                    review_status="validated",
                ),
                SemanticAsset(
                    type="policy_rule",
                    text="rejected",
                    confidence=0.1,
                    review_status="rejected",
                ),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        statuses = {a.review_status for a in out.assets}
        assert statuses == {"needs_review", "source_backed", "validated", "rejected"}

    def test_exporter_does_not_drop_unvalidated_content(self) -> None:
        # Acceptance criterion 4: "Keep unvalidated outputs clearly
        # marked." We satisfy that by labelling, not by omission.
        sem = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="t")],
            validation_status="needs_review",
            assets=[
                SemanticAsset(
                    type="policy_rule",
                    text="rejected",
                    confidence=0.1,
                    review_status="rejected",
                ),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        assert out.manifest.chunk_count == 1
        assert out.manifest.asset_count == 1


# ── Package-level checksum ────────────────────────────────────────────


class TestPackageSha256:
    def test_same_payload_same_hash(self) -> None:
        sem = _semantic(
            sections=[
                SemanticSection(id="s-1", heading="H", text="alpha"),
                SemanticSection(id="s-2", heading="H2", text="beta"),
            ],
        )
        v = _version()
        a = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem)
        b = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem)
        assert a.manifest.package_sha256 == b.manifest.package_sha256

    def test_section_reordering_does_not_change_hash(self) -> None:
        # Sorting at the top level of ``_package_sha256`` makes the
        # hash reorder-stable. Otherwise a re-extraction that walks
        # the parser tree in a different order would invalidate every
        # consumer's cache.
        s1 = SemanticSection(id="s-1", heading="H", text="alpha")
        s2 = SemanticSection(id="s-2", heading="H2", text="beta")
        sem_a = _semantic(sections=[s1, s2])
        sem_b = _semantic(sections=[s2, s1])
        v = _version()
        a = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_a)
        b = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_b)
        assert a.manifest.package_sha256 == b.manifest.package_sha256

    def test_content_change_changes_hash(self) -> None:
        sem_a = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="alpha")],
        )
        sem_b = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="alpha-EDIT")],
        )
        v = _version()
        a = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_a)
        b = _exporter()._build(document_or_filename="f.pdf", version=v, semantic=sem_b)
        assert a.manifest.package_sha256 != b.manifest.package_sha256

    def test_hash_is_canonical_json_sha256(self) -> None:
        # Sanity check the algorithm matches the documented contract:
        # canonical_json({chunks: sorted, assets: sorted}) → sha256.
        sem = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="alpha")],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        recomputed = _package_sha256(out.chunks, out.assets)
        assert out.manifest.package_sha256 == recomputed
        # Manually reproduce to lock the algorithm.
        chunk_payload = [
            c.model_dump(mode="json") for c in sorted(out.chunks, key=lambda c: c.chunk_id)
        ]
        canonical = json.dumps(
            {"chunks": chunk_payload, "assets": []},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == out.manifest.package_sha256


# ── Source references ride through ───────────────────────────────────


class TestSourceReferencesPreserved:
    def test_chunk_source_reference_ids_carried_verbatim(self) -> None:
        sem = _semantic(
            sections=[
                SemanticSection(
                    id="s-1",
                    heading="H",
                    text="t",
                    source_reference_ids=["src-p1-para0", "src-p1-para1"],
                ),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        assert out.chunks[0].source_reference_ids == ["src-p1-para0", "src-p1-para1"]

    def test_asset_source_reference_ids_carried_verbatim(self) -> None:
        sem = _semantic(
            assets=[
                SemanticAsset(
                    type="policy_rule",
                    text="rule.",
                    confidence=0.9,
                    review_status="source_backed",
                    source_reference_ids=["src-p3-para0"],
                ),
            ],
        )
        out = _exporter()._build(document_or_filename="f.pdf", version=_version(), semantic=sem)
        assert out.assets[0].source_reference_ids == ["src-p3-para0"]


# ── Smoke through public ``export()`` ────────────────────────────────


class _FakeDocumentService:
    """Stub matching the shape ``KnowledgeExporter`` calls into. Holds
    one document + one version so the public ``export()`` round-trip
    can be exercised without spinning up the real catalog stack.
    """

    def __init__(self, *, document, version) -> None:  # type: ignore[no-untyped-def]
        self._document = document
        self._version = version

    def get_document(self, document_id):  # type: ignore[no-untyped-def]
        return self._document if self._document.id == document_id else None

    def get_version(self, document_id, version_id):  # type: ignore[no-untyped-def]
        if self._version.document_id != document_id or self._version.id != version_id:
            raise KeyError("not found")
        return self._version


class _FakeSemanticOutputService:
    def __init__(self, *, semantic) -> None:  # type: ignore[no-untyped-def]
        self._semantic = semantic

    def get(self, *, document_id, version_id):  # type: ignore[no-untyped-def]
        return self._semantic


class TestExportPublicAPI:
    def test_export_round_trips_through_the_services(self) -> None:
        from app.schemas.document import Document

        document = Document(
            id="doc-1",
            original_filename="supplier-onboarding.pdf",
            latest_version_id="ver-1",
            versions=[],
        )
        version = _version()
        sem = _semantic(
            sections=[SemanticSection(id="s-1", heading="H", text="hello")],
        )
        exporter = KnowledgeExporter(
            documents=_FakeDocumentService(document=document, version=version),  # type: ignore[arg-type]
            semantic_outputs=_FakeSemanticOutputService(semantic=sem),  # type: ignore[arg-type]
        )
        package = exporter.export(document_id="doc-1", version_id="ver-1")
        assert package.manifest.original_filename == "supplier-onboarding.pdf"
        assert package.manifest.chunk_count == 1

    def test_missing_document_raises_keyerror(self) -> None:
        exporter = KnowledgeExporter(
            documents=_FakeDocumentService(  # type: ignore[arg-type]
                document=type("D", (), {"id": "other"})(),
                version=_version(),
            ),
            semantic_outputs=_FakeSemanticOutputService(  # type: ignore[arg-type]
                semantic=_semantic(),
            ),
        )
        with pytest.raises(KeyError):
            exporter.export(document_id="doc-1", version_id="ver-1")
