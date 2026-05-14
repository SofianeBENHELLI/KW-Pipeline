"""Tests for the semantic-generation method dispatch + LLM generator."""

from __future__ import annotations

from typing import Any

import pytest

from app.dependencies import build_services
from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticDocument
from app.services.semantic_generators import (
    DEFAULT_SEMANTIC_METHOD,
    SEMANTIC_METHOD_KNOWLEDGE_GRAPH,
    SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE,
    SEMANTIC_METHOD_STRUCTURE_FIRST,
    KnowledgeGraphSemanticGenerator,
    SemanticIntelligenceGenerator,
    StructureFirstSemanticGenerator,
    _AssetWire,
    _KNOWLEDGE_GRAPH_ASSET_TYPES,
    _ProfileWire,
    _SEMANTIC_INTELLIGENCE_ASSET_TYPES,
    _SemanticEnvelope,
)
from app.services.semantic_output_service import (
    SemanticGenerationFailed,
    SemanticOutputService,
    UnknownSemanticMethod,
)


def _upload(services, content: bytes = b"policy text body") -> tuple[str, str]:
    version = services.documents.upload("policy.txt", "text/plain", content)
    return version.document_id, version.id


class _FakeCompletion:
    """Stand-in for an instructor completion (only `usage` is consumed)."""

    def __init__(self, *, input_tokens: int = 10, output_tokens: int = 5) -> None:
        self.usage = type(
            "Usage",
            (),
            {"input_tokens": input_tokens, "output_tokens": output_tokens},
        )()


class _FakeInstructorClient:
    """Returns a queued envelope; records the prompt for assertion."""

    def __init__(self, envelope: _SemanticEnvelope) -> None:
        self._envelope = envelope
        self.calls: list[dict[str, Any]] = []

    def create_with_completion(
        self,
        *,
        response_model,
        messages,
        max_retries: int = 1,
        max_tokens: int = 1024,
    ):
        self.calls.append(
            {
                "response_model": response_model,
                "messages": messages,
                "max_retries": max_retries,
                "max_tokens": max_tokens,
            },
        )
        return self._envelope, _FakeCompletion()


class _RaisingInstructorClient:
    def create_with_completion(self, **_: Any):
        raise RuntimeError("upstream unavailable")


def _envelope_with_one_asset(section_id: str) -> _SemanticEnvelope:
    return _SemanticEnvelope(
        profile=_ProfileWire(
            title="Supplier Onboarding Policy",
            document_type="policy",
            purpose="Define onboarding gates for new suppliers.",
            audience="Procurement",
            executive_summary="A short summary.",
        ),
        assets=[
            _AssetWire(
                type="requirement",
                text="New suppliers must complete the security questionnaire.",
                confidence=0.9,
                source_reference_ids=[section_id],
            ),
        ],
    )


# ── Service-level dispatch ───────────────────────────────────────────


class TestSemanticOutputServiceDispatch:
    def test_default_method_runs_deterministic_and_stamps_method_id(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id
        )

        out = services.semantic_outputs.generate(
            document_id=document_id, version_id=version_id
        )

        # Deterministic is the default; the adapter must stamp the
        # method id on the persisted row so the registry stays in sync
        # with what's on disk.
        assert out.extraction_method == SEMANTIC_METHOD_STRUCTURE_FIRST

    def test_unknown_method_raises_unknownsemanticmethod(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id
        )

        with pytest.raises(UnknownSemanticMethod):
            services.semantic_outputs.generate(
                document_id=document_id, version_id=version_id, method="bogus"
            )

    def test_method_omitted_returns_cached_regardless_of_method(self):
        # Pre-method-dispatch callers (method=None) must keep getting
        # the cache-first behaviour the original service shipped.
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id
        )

        first = services.semantic_outputs.generate(
            document_id=document_id, version_id=version_id
        )
        second = services.semantic_outputs.generate(
            document_id=document_id, version_id=version_id
        )
        assert second.id == first.id

    def test_method_change_regenerates_persisted_row(self):
        """Switching from deterministic → llm overwrites the cached row."""
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id
        )
        deterministic_first = services.semantic_outputs.generate(
            document_id=document_id,
            version_id=version_id,
            method=SEMANTIC_METHOD_STRUCTURE_FIRST,
        )

        # Splice an LLM generator in. A real wiring resolves this from
        # instructor + provider keys; tests use the fake.
        section_id = deterministic_first.sections[0].id
        fake_client = _FakeInstructorClient(_envelope_with_one_asset(section_id))
        llm_generator = SemanticIntelligenceGenerator(
            client=fake_client, model="test/fake"
        )
        # Re-wire the service registry — the simplest test affordance
        # is to mutate the private dict because the field is built once
        # at construction time. Production paths inject via
        # ``generators=``.
        services.semantic_outputs._generators[SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE] = llm_generator

        llm_out = services.semantic_outputs.generate(
            document_id=document_id,
            version_id=version_id,
            method=SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE,
        )

        assert llm_out.extraction_method == SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE
        # LLM-emitted profile overrides the filename-derived title.
        assert llm_out.document_profile.title == "Supplier Onboarding Policy"
        # The LLM asset survived the section-id allow-list check.
        assert len(llm_out.assets) == 1
        assert llm_out.assets[0].source_reference_ids == [section_id]
        # Persisted row really got rewritten.
        re_read = services.semantic_outputs.get(
            document_id=document_id, version_id=version_id
        )
        assert re_read.extraction_method == SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE

    def test_available_methods_lists_deterministic_first(self):
        services = build_services()
        methods = services.semantic_outputs.available_methods
        assert methods[0] == SEMANTIC_METHOD_STRUCTURE_FIRST

    def test_default_constant_is_structure_first_per_product_decision(self):
        # 2026-05-14 product decision: dropdown opens on Method 1.
        assert DEFAULT_SEMANTIC_METHOD == SEMANTIC_METHOD_STRUCTURE_FIRST


# ── SemanticIntelligenceGenerator ─────────────────────────────────────────────


def _raw_extraction_with_section(section_id: str) -> RawExtraction:
    return RawExtraction.model_validate(
        {
            "document_version_id": "ver-x",
            "parser_name": "fake",
            "parser_version": "0.0",
            "text": "Some body content.",
            "sections": [
                {
                    "id": section_id,
                    "heading": "Policy",
                    "text": "Suppliers must complete the form.",
                    "source_reference_ids": ["src-1"],
                },
            ],
            "warnings": [],
            "source_references": [
                {
                    "id": "src-1",
                    "document_version_id": "ver-x",
                    "section_id": section_id,
                    "page_number": 1,
                    "snippet": "Suppliers must complete the form.",
                },
            ],
        },
    )


def _version() -> DocumentVersion:
    return DocumentVersion.model_validate(
        {
            "id": "ver-x",
            "document_id": "doc-x",
            "version_number": 1,
            "filename": "policy.pdf",
            "content_type": "application/pdf",
            "file_size": 1024,
            "sha256": "0" * 64,
            "storage_uri": "memory://policy.pdf",
            "status": "EXTRACTED",
            "duplicate_of_version_id": None,
            "failure_reason": None,
            "reviewer_note": None,
            "reviewed_at": None,
            "created_at": "2026-05-14T12:00:00Z",
        },
    )


class TestSemanticIntelligenceGenerator:
    def test_preserves_parser_sections_verbatim(self):
        raw = _raw_extraction_with_section("sec-1")
        client = _FakeInstructorClient(_envelope_with_one_asset("sec-1"))
        gen = SemanticIntelligenceGenerator(client=client, model="test/fake")

        result = gen.generate(version=_version(), raw_extraction=raw)

        # Section text + source lineage come straight from the parser.
        assert [s.id for s in result.sections] == ["sec-1"]
        assert result.sections[0].text == "Suppliers must complete the form."
        assert result.sections[0].source_reference_ids == ["src-1"]

    def test_drops_assets_with_hallucinated_section_ids(self):
        raw = _raw_extraction_with_section("sec-1")
        envelope = _SemanticEnvelope(
            profile=_ProfileWire(
                title="Doc", document_type="report",
            ),
            assets=[
                _AssetWire(
                    type="claim",
                    text="grounded",
                    confidence=0.5,
                    source_reference_ids=["sec-1"],
                ),
                _AssetWire(
                    type="claim",
                    text="hallucinated",
                    confidence=0.5,
                    source_reference_ids=["sec-does-not-exist"],
                ),
            ],
        )
        gen = SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(envelope), model="test/fake"
        )

        result = gen.generate(version=_version(), raw_extraction=raw)
        assert [a.text for a in result.assets] == ["grounded"]

    def test_forces_needs_review_on_assets(self):
        raw = _raw_extraction_with_section("sec-1")
        envelope = _SemanticEnvelope(
            profile=_ProfileWire(title="Doc", document_type="report"),
            assets=[
                _AssetWire(
                    type="claim",
                    text="t",
                    confidence=0.5,
                    source_reference_ids=["sec-1"],
                ),
            ],
        )
        gen = SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(envelope), model="test/fake"
        )

        result = gen.generate(version=_version(), raw_extraction=raw)
        assert result.assets[0].review_status == "needs_review"

    def test_llm_failure_raises_runtimeerror(self):
        raw = _raw_extraction_with_section("sec-1")
        gen = SemanticIntelligenceGenerator(
            client=_RaisingInstructorClient(), model="test/fake"
        )
        with pytest.raises(RuntimeError, match="LLM semantic generation failed"):
            gen.generate(version=_version(), raw_extraction=raw)

    def test_method_id_is_semantic_intelligence(self):
        raw = _raw_extraction_with_section("sec-1")
        gen = SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(_envelope_with_one_asset("sec-1")),
            model="test/fake",
        )
        result = gen.generate(version=_version(), raw_extraction=raw)
        assert result.extraction_method == SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE

    def test_semantic_intelligence_prompt_lists_method_2_asset_types(self):
        gen = SemanticIntelligenceGenerator(
            client=_FakeInstructorClient(_envelope_with_one_asset("sec-1")),
            model="test/fake",
        )
        # The Method 2 taxonomy must be present in the system prompt so
        # the LLM uses the right vocabulary. The Method 3 widened types
        # (e.g. ``open_question``) should NOT leak in.
        assert "requirement" in gen._system_prompt
        assert "open_question" not in gen._system_prompt

    def test_service_maps_generator_runtimeerror_to_semanticgenerationfailed(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id
        )
        # Inject a generator that always raises.
        services.semantic_outputs._generators[SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE] = (
            SemanticIntelligenceGenerator(
                client=_RaisingInstructorClient(), model="test/fake"
            )
        )
        with pytest.raises(SemanticGenerationFailed):
            services.semantic_outputs.generate(
                document_id=document_id,
                version_id=version_id,
                method=SEMANTIC_METHOD_SEMANTIC_INTELLIGENCE,
            )


class TestKnowledgeGraphSemanticGenerator:
    """Method 3 — uses the same wire envelope but the wider taxonomy."""

    def test_method_id_is_knowledge_graph(self):
        raw = _raw_extraction_with_section("sec-1")
        gen = KnowledgeGraphSemanticGenerator(
            client=_FakeInstructorClient(_envelope_with_one_asset("sec-1")),
            model="test/fake",
        )
        result = gen.generate(version=_version(), raw_extraction=raw)
        assert result.extraction_method == SEMANTIC_METHOD_KNOWLEDGE_GRAPH

    def test_widened_taxonomy_listed_in_system_prompt(self):
        gen = KnowledgeGraphSemanticGenerator(
            client=_FakeInstructorClient(_envelope_with_one_asset("sec-1")),
            model="test/fake",
        )
        # Spot-check the widened types that Method 2 does not advertise.
        for asset_type in (
            "kpi",
            "assumption",
            "dependency",
            "business_value",
            "technical_capability",
            "open_question",
        ):
            assert asset_type in gen._system_prompt

    def test_taxonomy_constants_are_disjoint_supersets(self):
        # Sanity: every Method 2 type should appear in the Method 3
        # taxonomy (Method 3 is a superset for graph projection).
        m2 = set(_SEMANTIC_INTELLIGENCE_ASSET_TYPES)
        m3 = set(_KNOWLEDGE_GRAPH_ASSET_TYPES)
        # Every Method 2 base type is either present verbatim or
        # canonicalised to its Method 3 equivalent — we don't enforce
        # full inclusion (``action_item`` becomes ``action`` in M3) but
        # the overlap must not be empty.
        assert m2 & m3, "Methods 2 and 3 should share at least some types"
        # And the widened Method 3 types must add at least 5 new keys.
        assert len(m3 - m2) >= 5

    def test_method_3_via_service_dispatch(self):
        services = build_services()
        document_id, version_id = _upload(services)
        services.extraction_jobs.extract(
            document_id=document_id, version_id=version_id
        )
        # Bootstrap a cached deterministic row so we can read the
        # generated section id, then plug in the Method 3 generator.
        first = services.semantic_outputs.generate(
            document_id=document_id, version_id=version_id
        )
        section_id = first.sections[0].id
        services.semantic_outputs._generators[SEMANTIC_METHOD_KNOWLEDGE_GRAPH] = (
            KnowledgeGraphSemanticGenerator(
                client=_FakeInstructorClient(
                    _SemanticEnvelope(
                        profile=_ProfileWire(
                            title="Supplier Onboarding Policy",
                            document_type="policy",
                        ),
                        assets=[
                            _AssetWire(
                                type="dependency",
                                text="Onboarding depends on legal review.",
                                confidence=0.9,
                                source_reference_ids=[section_id],
                            ),
                            _AssetWire(
                                type="open_question",
                                text="Is the SLA contractual?",
                                confidence=0.7,
                                source_reference_ids=[section_id],
                            ),
                        ],
                    ),
                ),
                model="test/fake",
            )
        )
        out = services.semantic_outputs.generate(
            document_id=document_id,
            version_id=version_id,
            method=SEMANTIC_METHOD_KNOWLEDGE_GRAPH,
        )
        assert out.extraction_method == SEMANTIC_METHOD_KNOWLEDGE_GRAPH
        assert [a.type for a in out.assets] == ["dependency", "open_question"]
