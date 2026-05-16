"""Tests for ``BusinessTaxonomyAllocator`` (EPIC-1 slice 1.3, #340).

Mirrors the structure of ``test_topic_extractor.py``: an in-process
fake instructor client returns canned ``AllocationEnvelope``
responses; we assert on the allocator's prompt assembly, parsing,
filtering, and on the projector hook the operator workflow depends
on. No network calls.

Coverage:

* Empty / whitespace-only documents skip the LLM call entirely.
* Empty taxonomy short-circuits before any LLM call.
* The prompt cites every allowed category id and every deterministic
  concept extracted from the chunk.
* Valid responses parse into :class:`ChunkTaxonomyAllocation`
  instances with the assignment list intact.
* Assignments citing unknown category ids are filtered;
  partial-valid assignments survive.
* The per-chunk assignment cap (5) trims defensively.
* Per-chunk failure produces an empty-assignments row, not a raise.
* Token cap truncates the chunk body but not the category block.
* Taxonomy fingerprint is stable across runs with the same taxonomy.
* Prompt hash is stable across runs with the same (taxonomy, chunk).
* ``_maybe_build_business_taxonomy_allocator`` returns ``None``
  without the kill switch / without an LLM.
* Projector hook fires only when all three of (allocator, store,
  taxonomy) are wired AND the taxonomy is non-empty.
* Re-projection deletes prior allocations for the same version_id.
* Allocator failures are swallowed by the projector hook (fire-and-log).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from app.dependencies import _maybe_build_business_taxonomy_allocator
from app.models.document import DocumentVersionStatus
from app.schemas.chunk_taxonomy_allocation import ChunkTaxonomyAllocation
from app.schemas.document import Document, DocumentVersion
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.schemas.taxonomy import Taxonomy, TaxonomyCategory
from app.services.business_taxonomy_allocator import (
    AssignmentWire,
    BusinessTaxonomyAllocator,
)
from app.services.chunk_taxonomy_allocation_store import (
    InMemoryChunkTaxonomyAllocationStore,
)
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.projector import KnowledgeProjector

# ─── Test doubles ────────────────────────────────────────────────────


class _FakeUsage:
    def __init__(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeCompletion:
    def __init__(self, usage: _FakeUsage | None = None) -> None:
        self.usage = usage or _FakeUsage()


class _FakeInstructorClient:
    """In-process stand-in for an ``instructor.Instructor`` client.

    Tests enqueue allocation dicts (one per expected LLM call — the
    allocator makes one call per non-empty chunk). The fake
    materialises them through :class:`AllocationEnvelope` so any
    shape error surfaces here exactly the way it would in production.
    """

    name: str = "fake-instructor"

    def __init__(self) -> None:
        self._responses: list[tuple[dict[str, Any], _FakeUsage]] = []
        self.calls: list[dict[str, Any]] = []
        self._raise_exc: Exception | None = None

    def enqueue(
        self,
        envelope_dict: dict[str, Any],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._responses.append(
            (envelope_dict, _FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens))
        )

    def fail_with(self, exc: Exception) -> None:
        self._raise_exc = exc

    def create_with_completion(
        self,
        *,
        response_model: type[BaseModel],
        messages: list[dict[str, str]],
        max_retries: int = 2,
        max_tokens: int = 2048,
    ) -> tuple[Any, _FakeCompletion]:
        self.calls.append(
            {
                "response_model": response_model,
                "messages": messages,
                "max_retries": max_retries,
                "max_tokens": max_tokens,
                "system": next((m["content"] for m in messages if m["role"] == "system"), ""),
                "user": next((m["content"] for m in messages if m["role"] == "user"), ""),
            }
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        if not self._responses:
            raise RuntimeError(
                "_FakeInstructorClient: no recorded responses left. Call "
                "enqueue(...) once per expected LLM call."
            )
        envelope_dict, usage = self._responses.pop(0)
        envelope = response_model.model_validate(envelope_dict)
        return envelope, _FakeCompletion(usage=usage)


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
        created_at=datetime(2026, 5, 16, tzinfo=UTC),
    )


def _make_taxonomy() -> Taxonomy:
    return Taxonomy(
        categories=[
            TaxonomyCategory(
                id="hr",
                label="Human Resources",
                description="HR policies and procedures.",
                subcategories=[
                    TaxonomyCategory(
                        id="hr.hybrid_work",
                        label="Hybrid Work",
                        description="Policies about remote / on-site mix.",
                    ),
                ],
            ),
            TaxonomyCategory(
                id="safety",
                label="Safety",
                description="Occupational safety standards.",
            ),
        ]
    )


# ─── Allocator unit tests ────────────────────────────────────────────


def test_allocate_returns_parsed_assignments() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "assignments": [
                {
                    "category_id": "hr.hybrid_work",
                    "confidence": 0.91,
                    "rationale": "The chunk discusses hybrid work schedules.",
                    "supporting_concept_texts": ["hybrid", "remote"],
                }
            ]
        },
        input_tokens=200,
        output_tokens=40,
    )
    allocator = BusinessTaxonomyAllocator(client=fake, model="claude-sonnet-4-5")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="Hybrid work", text="hybrid remote.")],
    )

    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )

    assert len(allocations) == 1
    alloc = allocations[0]
    assert alloc.id == "alloc-ver-1-s1"
    assert alloc.chunk_id == "s1"
    assert alloc.section_id == "s1"
    assert alloc.document_id == "doc-1"
    assert alloc.version_id == "ver-1"
    assert alloc.model_id == "claude-sonnet-4-5"
    assert len(alloc.assignments) == 1
    a = alloc.assignments[0]
    assert a.category_id == "hr.hybrid_work"
    assert a.confidence == 0.91
    assert a.rationale.startswith("The chunk discusses")
    assert a.supporting_concept_texts == ["hybrid", "remote"]


def test_allocate_skips_empty_document_without_llm_call() -> None:
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(version=version, sections=[])
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert allocations == []
    assert fake.calls == []


def test_allocate_skips_whitespace_only_sections_without_llm_call() -> None:
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="     ")],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert allocations == []
    assert fake.calls == []


def test_allocate_short_circuits_on_empty_taxonomy() -> None:
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=Taxonomy(categories=[]),
    )
    assert allocations == []
    assert fake.calls == []


def test_allocate_one_llm_call_per_non_empty_chunk() -> None:
    fake = _FakeInstructorClient()
    for _ in range(2):
        fake.enqueue({"assignments": []})  # both chunks abstain
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="content one"),
            SemanticSection(id="s2", heading="B", text="    "),  # skipped
            SemanticSection(id="s3", heading="C", text="content three"),
        ],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert len(allocations) == 2
    assert len(fake.calls) == 2


def test_prompt_carries_every_taxonomy_id_in_allowed_pool() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue({"assignments": []})
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    user_prompt = fake.calls[0]["user"]
    assert "Allowed category_ids: [hr, hr.hybrid_work, safety]" in user_prompt
    # Category labels + descriptions land in the prompt body too.
    assert "Hybrid Work" in user_prompt
    assert "Occupational safety standards." in user_prompt


def test_allocate_drops_assignments_citing_unknown_category_ids() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "assignments": [
                {
                    "category_id": "hr.hybrid_work",
                    "confidence": 0.9,
                    "rationale": "good match",
                },
                {
                    "category_id": "made.up.category",
                    "confidence": 0.7,
                    "rationale": "hallucinated id",
                },
            ]
        }
    )
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert len(allocations) == 1
    assert [a.category_id for a in allocations[0].assignments] == ["hr.hybrid_work"]


def test_allocate_caps_assignments_per_chunk() -> None:
    """The LLM may overshoot the prompt's "1–3 assignments" hint; we
    cap at 5 defensively."""
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "assignments": [
                {
                    "category_id": "hr",
                    "confidence": 0.8,
                    "rationale": f"reason {i}",
                }
                for i in range(10)
            ]
        }
    )
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert len(allocations[0].assignments) == 5


def test_allocate_writes_empty_row_when_llm_returns_no_matches() -> None:
    """An empty ``assignments`` list is a meaningful audit signal —
    the row is still persisted with fingerprint / model / prompt
    metadata so the operator knows the allocator ran."""
    fake = _FakeInstructorClient()
    fake.enqueue({"assignments": []})
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert len(allocations) == 1
    assert allocations[0].assignments == []
    # Fingerprint / model / prompt-hash still populated.
    assert allocations[0].taxonomy_fingerprint
    assert allocations[0].model_id == "m"
    assert allocations[0].prompt_hash


def test_allocate_swallows_per_chunk_failure() -> None:
    """A per-chunk LLM error yields an empty-assignments row, not a
    raise — fire-and-log boundary applied per chunk."""
    fake = _FakeInstructorClient()
    fake.fail_with(RuntimeError("LLM timed out"))
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    allocations = allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    assert len(allocations) == 1
    assert allocations[0].assignments == []


def test_allocate_rejects_mismatched_version() -> None:
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version(version_id="ver-1")
    other_version = _make_version(version_id="ver-OTHER")
    semantic = _make_semantic(
        version=other_version,
        sections=[SemanticSection(id="s1", heading="A", text="x")],
    )
    with pytest.raises(ValueError):
        allocator.allocate(
            semantic,
            document=_make_document(version),
            version=version,
            taxonomy=_make_taxonomy(),
        )


def test_allocate_negative_token_cap_is_rejected() -> None:
    with pytest.raises(ValueError):
        BusinessTaxonomyAllocator(
            client=_FakeInstructorClient(),
            model="m",
            max_input_tokens=-1,
        )


def test_allocate_token_cap_truncates_chunk_body_only() -> None:
    """The cap truncates the chunk body; the category block + allowed
    ids line are load-bearing and must survive verbatim."""
    fake = _FakeInstructorClient()
    fake.enqueue({"assignments": []})
    allocator = BusinessTaxonomyAllocator(client=fake, model="m", max_input_tokens=50)
    version = _make_version()
    long_text = "alpha beta gamma delta epsilon zeta eta theta " * 50
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="Long", text=long_text)],
    )
    allocator.allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=_make_taxonomy(),
    )
    user_prompt = fake.calls[0]["user"]
    # Allowed ids line and category descriptions survive verbatim.
    assert "Allowed category_ids: [hr, hr.hybrid_work, safety]" in user_prompt
    assert "Occupational safety standards." in user_prompt
    # Chunk body is truncated.
    body_start = user_prompt.index("Chunk body")
    body = user_prompt[body_start:]
    # The full long_text would be >2000 chars; truncation keeps the
    # post-"Chunk body" tail small.
    assert len(body) < 500


# ─── Fingerprint + prompt-hash determinism ───────────────────────────


def test_taxonomy_fingerprint_is_stable_across_runs() -> None:
    fake1 = _FakeInstructorClient()
    fake2 = _FakeInstructorClient()
    fake1.enqueue({"assignments": []})
    fake2.enqueue({"assignments": []})
    taxonomy = _make_taxonomy()
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    a1 = BusinessTaxonomyAllocator(client=fake1, model="m").allocate(
        semantic, document=_make_document(version), version=version, taxonomy=taxonomy
    )
    a2 = BusinessTaxonomyAllocator(client=fake2, model="m").allocate(
        semantic, document=_make_document(version), version=version, taxonomy=taxonomy
    )
    assert a1[0].taxonomy_fingerprint == a2[0].taxonomy_fingerprint


def test_taxonomy_fingerprint_changes_when_taxonomy_changes() -> None:
    fake1 = _FakeInstructorClient()
    fake2 = _FakeInstructorClient()
    fake1.enqueue({"assignments": []})
    fake2.enqueue({"assignments": []})
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    tax_a = _make_taxonomy()
    tax_b = Taxonomy(
        categories=[
            TaxonomyCategory(
                id="hr",
                label="Human Resources",
                description="DIFFERENT description triggers fingerprint change.",
            ),
        ]
    )
    a1 = BusinessTaxonomyAllocator(client=fake1, model="m").allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=tax_a,
    )
    a2 = BusinessTaxonomyAllocator(client=fake2, model="m").allocate(
        semantic,
        document=_make_document(version),
        version=version,
        taxonomy=tax_b,
    )
    assert a1[0].taxonomy_fingerprint != a2[0].taxonomy_fingerprint


def test_prompt_hash_is_stable_across_runs() -> None:
    fake1 = _FakeInstructorClient()
    fake2 = _FakeInstructorClient()
    fake1.enqueue({"assignments": []})
    fake2.enqueue({"assignments": []})
    taxonomy = _make_taxonomy()
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    a1 = BusinessTaxonomyAllocator(client=fake1, model="m").allocate(
        semantic, document=_make_document(version), version=version, taxonomy=taxonomy
    )
    a2 = BusinessTaxonomyAllocator(client=fake2, model="m").allocate(
        semantic, document=_make_document(version), version=version, taxonomy=taxonomy
    )
    assert a1[0].prompt_hash == a2[0].prompt_hash


# ─── Schema validators ───────────────────────────────────────────────


def test_assignment_wire_rejects_empty_rationale() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AssignmentWire(category_id="x", confidence=0.5, rationale="")


# ─── Factory ─────────────────────────────────────────────────────────


def test_maybe_build_business_taxonomy_allocator_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KW_BUSINESS_TAXONOMY_ALLOCATOR_ENABLED", raising=False)
    extractor = _maybe_build_business_taxonomy_allocator()
    assert extractor is None


def test_maybe_build_business_taxonomy_allocator_needs_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KW_BUSINESS_TAXONOMY_ALLOCATOR_ENABLED", "true")
    monkeypatch.setenv("KW_KNOWLEDGE_LAYER_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    extractor = _maybe_build_business_taxonomy_allocator()
    assert extractor is None


def test_maybe_build_business_taxonomy_allocator_built_when_client_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KW_BUSINESS_TAXONOMY_ALLOCATOR_ENABLED", "true")
    fake = _FakeInstructorClient()
    extractor = _maybe_build_business_taxonomy_allocator(client=fake, model="claude-sonnet-4-5")
    assert isinstance(extractor, BusinessTaxonomyAllocator)


# ─── Projector hook integration ──────────────────────────────────────


def _run_projector(
    *,
    allocator: BusinessTaxonomyAllocator | None,
    store: InMemoryChunkTaxonomyAllocationStore | None,
    taxonomy: Taxonomy | None,
    version: DocumentVersion,
    sections: list[SemanticSection] | None = None,
) -> InMemoryChunkTaxonomyAllocationStore:
    sections = (
        sections
        if sections is not None
        else [SemanticSection(id="s1", heading="A", text="content about hybrid work")]
    )
    document = _make_document(version)
    semantic = _make_semantic(version=version, sections=sections)
    projector = KnowledgeProjector(
        graph_store=InMemoryGraphStore(),
        business_taxonomy_allocator=allocator,
        chunk_taxonomy_allocation_store=store,
        active_taxonomy=taxonomy,
    )
    projector.project(document=document, version=version, semantic=semantic)
    return store if store is not None else InMemoryChunkTaxonomyAllocationStore()


def test_projector_hook_writes_allocations_when_all_three_wired() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "assignments": [
                {
                    "category_id": "hr.hybrid_work",
                    "confidence": 0.9,
                    "rationale": "hybrid work content",
                }
            ]
        }
    )
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    store = InMemoryChunkTaxonomyAllocationStore()
    version = _make_version()
    _run_projector(
        allocator=allocator,
        store=store,
        taxonomy=_make_taxonomy(),
        version=version,
    )
    items, _ = store.list_for_document(version.document_id)
    assert len(items) == 1
    assert items[0].assignments[0].category_id == "hr.hybrid_work"


def test_projector_hook_skipped_when_store_missing() -> None:
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    version = _make_version()
    _run_projector(
        allocator=allocator,
        store=None,
        taxonomy=_make_taxonomy(),
        version=version,
    )
    assert fake.calls == []


def test_projector_hook_skipped_when_allocator_missing() -> None:
    store = InMemoryChunkTaxonomyAllocationStore()
    version = _make_version()
    _run_projector(
        allocator=None,
        store=store,
        taxonomy=_make_taxonomy(),
        version=version,
    )
    items, _ = store.list_all()
    assert items == []


def test_projector_hook_skipped_when_taxonomy_missing() -> None:
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    store = InMemoryChunkTaxonomyAllocationStore()
    version = _make_version()
    _run_projector(
        allocator=allocator,
        store=store,
        taxonomy=None,
        version=version,
    )
    assert fake.calls == []
    items, _ = store.list_all()
    assert items == []


def test_projector_hook_skipped_when_taxonomy_empty() -> None:
    """An empty taxonomy is a deployed-but-unconfigured state — the
    LLM has nothing to map chunks against."""
    fake = _FakeInstructorClient()
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    store = InMemoryChunkTaxonomyAllocationStore()
    version = _make_version()
    _run_projector(
        allocator=allocator,
        store=store,
        taxonomy=Taxonomy(categories=[]),
        version=version,
    )
    assert fake.calls == []


def test_projector_hook_replaces_allocations_on_re_projection() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "assignments": [
                {
                    "category_id": "hr.hybrid_work",
                    "confidence": 0.9,
                    "rationale": "first run",
                }
            ]
        }
    )
    fake.enqueue(
        {
            "assignments": [
                {
                    "category_id": "safety",
                    "confidence": 0.85,
                    "rationale": "second run, different category",
                }
            ]
        }
    )
    allocator = BusinessTaxonomyAllocator(client=fake, model="m")
    store = InMemoryChunkTaxonomyAllocationStore()
    version = _make_version()
    _run_projector(
        allocator=allocator,
        store=store,
        taxonomy=_make_taxonomy(),
        version=version,
    )
    _run_projector(
        allocator=allocator,
        store=store,
        taxonomy=_make_taxonomy(),
        version=version,
    )
    items, _ = store.list_all()
    # The first batch was deleted before the second was saved — only
    # the second run's category survives.
    assert len(items) == 1
    assert items[0].assignments[0].category_id == "safety"


def test_projector_hook_swallows_allocator_failure() -> None:
    """A catastrophic allocator failure (e.g. an exception escaping
    the per-chunk try/except) is caught by the projector hook's
    outer try/except — the structural projection still succeeds."""

    class _BrokenAllocator(BusinessTaxonomyAllocator):
        def allocate(  # type: ignore[override]
            self, *args: Any, **kwargs: Any
        ) -> list[ChunkTaxonomyAllocation]:
            raise RuntimeError("allocator imploded")

    allocator = _BrokenAllocator(client=_FakeInstructorClient(), model="m")
    store = InMemoryChunkTaxonomyAllocationStore()
    version = _make_version()
    # No raise — projector swallows the failure.
    _run_projector(
        allocator=allocator,
        store=store,
        taxonomy=_make_taxonomy(),
        version=version,
    )
    items, _ = store.list_all()
    assert items == []
