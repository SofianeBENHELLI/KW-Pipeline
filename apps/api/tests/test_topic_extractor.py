"""Tests for ``TopicExtractor`` (#411, ADR-031).

Mirrors the structure of ``test_claim_extractor.py``: an in-process
stub LLM client returns canned tool-call responses; we assert on the
extractor's parsing + filtering behaviour and on the projector hook
the operator workflow depends on. No network calls.

Coverage:

* Empty / whitespace-only documents skip the LLM call entirely.
* Valid responses parse into :class:`DocumentTopic` instances.
* The prompt cites every non-empty section id as allowed provenance.
* Topics with no ``supporting_chunk_ids`` are dropped.
* Topics citing unknown section ids have those ids filtered; if
  nothing valid remains the topic is dropped.
* Per-document token guard: when the cap is set and the assembled
  prompt body exceeds it, every section's text is truncated
  proportionally so every section is still represented (no skip).
* ``_maybe_build_topic_extractor`` returns ``None`` without an LLM
  and a real extractor when the LLM is wired.
* Projector hook fires only when both extractor + store are set.
* Re-projection deletes prior topics for the same ``version_id``.
* Extractor failures are swallowed by the projector hook (fire-and-log).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.dependencies import _maybe_build_topic_extractor
from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.schemas.document_topic import DocumentTopic
from app.schemas.semantic_document import (
    DocumentProfile,
    SemanticDocument,
    SemanticSection,
)
from app.services.document_topic_store import InMemoryDocumentTopicStore
from app.services.knowledge.graph_store import InMemoryGraphStore
from app.services.knowledge.llm_client import LLMClient
from app.services.knowledge.projector import KnowledgeProjector
from app.services.topic_extractor import TopicExtractor

# ─── Test doubles ────────────────────────────────────────────────────


class _StubLLM:
    """In-process ``LLMClient`` that returns canned responses per call."""

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
        # Not used by TopicExtractor; included for Protocol parity.
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


def test_extract_returns_parsed_topics() -> None:
    stub = _StubLLM()
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "Microservices architecture",
                    "summary": "How services split across the platform.",
                    "keywords": ["microservices", "platform"],
                    "confidence": 0.92,
                    "supporting_chunk_ids": ["s1", "s2"],
                }
            ]
        },
        {"input_tokens": 1200, "output_tokens": 80},
    )
    extractor = TopicExtractor(llm=stub, model="claude-sonnet-4-5")

    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="Intro", text="Overview of the system."),
            SemanticSection(id="s2", heading="Services", text="Each service is a microservice."),
        ],
    )

    topics = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )

    assert len(topics) == 1
    topic = topics[0]
    assert topic.label == "Microservices architecture"
    assert topic.summary == "How services split across the platform."
    assert topic.keywords == ["microservices", "platform"]
    assert topic.confidence == 0.92
    assert topic.supporting_chunk_ids == ["s1", "s2"]
    assert topic.id == f"topic-{version.id}-0"
    assert topic.document_id == version.document_id
    assert topic.version_id == version.id
    # One LLM call per document — not per section.
    assert len(stub.calls) == 1


def test_extract_skips_empty_document_without_llm_call() -> None:
    stub = _StubLLM()
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(version=version, sections=[])
    topics = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )
    assert topics == []
    assert stub.calls == []


def test_extract_skips_whitespace_only_document() -> None:
    stub = _StubLLM()
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="    ")],
    )
    topics = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )
    assert topics == []
    assert stub.calls == []


def test_prompt_carries_every_non_empty_section_id_in_allowed_pool() -> None:
    stub = _StubLLM()
    stub.enqueue({"topics": []})
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="content one"),
            SemanticSection(id="s2", heading="B", text="   "),  # excluded
            SemanticSection(id="s3", heading="C", text="content three"),
        ],
    )
    extractor.extract(semantic, document=_make_document(version), version=version)
    assert len(stub.calls) == 1
    user_prompt = stub.calls[0]["user"]
    assert "Allowed supporting_chunk_ids: [s1, s3]" in user_prompt
    assert "s2" not in user_prompt.split("Allowed supporting_chunk_ids:")[1].split("\n")[0]


def test_extract_drops_topics_without_provenance() -> None:
    stub = _StubLLM()
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "valid theme",
                    "summary": "ok",
                    "keywords": [],
                    "confidence": 0.8,
                    "supporting_chunk_ids": ["s1"],
                },
                {
                    "label": "missing provenance",
                    "summary": "should be dropped",
                    "keywords": [],
                    "confidence": 0.7,
                    "supporting_chunk_ids": [],
                },
            ]
        }
    )
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    assert [t.label for t in topics] == ["valid theme"]


def test_extract_drops_topics_citing_only_unknown_section_ids() -> None:
    stub = _StubLLM()
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "kept (some valid refs)",
                    "summary": "filters out s9 but keeps s1",
                    "keywords": [],
                    "confidence": 0.85,
                    "supporting_chunk_ids": ["s9", "s1"],
                },
                {
                    "label": "dropped (all hallucinated)",
                    "summary": "no real refs",
                    "keywords": [],
                    "confidence": 0.7,
                    "supporting_chunk_ids": ["s9", "s8"],
                },
            ]
        }
    )
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    assert len(topics) == 1
    assert topics[0].label == "kept (some valid refs)"
    assert topics[0].supporting_chunk_ids == ["s1"]


def test_extract_token_guard_truncates_proportionally() -> None:
    stub = _StubLLM()
    stub.enqueue({"topics": []})
    extractor = TopicExtractor(llm=stub, model="m", max_input_tokens=200)
    version = _make_version()
    # Two sections far longer than the cap; the truncation must keep
    # both represented in the prompt.
    semantic = _make_semantic(
        version=version,
        sections=[
            SemanticSection(id="s1", heading="A", text="alpha " * 500),
            SemanticSection(id="s2", heading="B", text="beta " * 500),
        ],
    )
    extractor.extract(semantic, document=_make_document(version), version=version)
    user_prompt = stub.calls[0]["user"]
    # Both sections are still in the prompt (truncation, not skip).
    assert "Section [s1]" in user_prompt
    assert "Section [s2]" in user_prompt
    # The body block was truncated — total length is bounded.
    body_start = user_prompt.index("Document body")
    body = user_prompt[body_start:]
    # Slack of one char per section + the framing "--- Section [...] ---" boilerplate.
    assert len(body) < 2000  # very loose upper bound; original was ~6000


def test_extract_token_guard_disabled_by_zero() -> None:
    stub = _StubLLM()
    stub.enqueue({"topics": []})
    extractor = TopicExtractor(llm=stub, model="m", max_input_tokens=0)
    version = _make_version()
    long_text = "alpha " * 5000
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text=long_text)],
    )
    extractor.extract(semantic, document=_make_document(version), version=version)
    user_prompt = stub.calls[0]["user"]
    # With the guard off, the full body lands in the prompt.
    assert long_text.strip() in user_prompt


def test_extract_negative_token_cap_is_rejected() -> None:
    with pytest.raises(ValueError):
        TopicExtractor(llm=_StubLLM(), model="m", max_input_tokens=-1)


def test_extract_swallows_llm_failure() -> None:
    stub = _StubLLM()
    stub.fail_with(RuntimeError("LLM timed out"))
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    # Per-document failure → empty list, no exception leaked.
    assert topics == []


def test_extract_rejects_mismatched_version() -> None:
    stub = _StubLLM()
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version(version_id="ver-1")
    other_version = _make_version(version_id="ver-OTHER")
    semantic = _make_semantic(
        version=other_version,
        sections=[SemanticSection(id="s1", heading="A", text="x")],
    )
    with pytest.raises(ValueError):
        extractor.extract(semantic, document=_make_document(version), version=version)


def test_extract_caps_topics_per_document() -> None:
    """Defensive trim — the LLM occasionally overshoots the prompt's
    "3 to 8 themes" hint. We cap at 12 in the extractor."""
    stub = _StubLLM()
    stub.enqueue(
        {
            "topics": [
                {
                    "label": f"Topic {i}",
                    "summary": "summary",
                    "keywords": [],
                    "confidence": 0.8,
                    "supporting_chunk_ids": ["s1"],
                }
                for i in range(20)
            ]
        }
    )
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    assert len(topics) == 12


# ─── Dependencies / factory ──────────────────────────────────────────


def test_maybe_build_topic_extractor_returns_none_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strip LLM keys so the factory short-circuits to None.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    extractor = _maybe_build_topic_extractor()
    assert extractor is None


def test_maybe_build_topic_extractor_built_when_llm_supplied() -> None:
    stub = _StubLLM()
    extractor = _maybe_build_topic_extractor(llm=stub, llm_model="claude-sonnet-4-5")
    assert isinstance(extractor, TopicExtractor)


# ─── Projector hook integration ──────────────────────────────────────


def _run_projector(
    *,
    extractor: TopicExtractor | None,
    store: InMemoryDocumentTopicStore | None,
    version: DocumentVersion,
    sections: list[SemanticSection] | None = None,
) -> InMemoryDocumentTopicStore:
    sections = (
        sections
        if sections is not None
        else [
            SemanticSection(id="s1", heading="A", text="content"),
        ]
    )
    document = _make_document(version)
    semantic = _make_semantic(version=version, sections=sections)
    projector = KnowledgeProjector(
        graph_store=InMemoryGraphStore(),
        topic_extractor=extractor,
        document_topic_store=store,
    )
    projector.project(document=document, version=version, semantic=semantic)
    return store if store is not None else InMemoryDocumentTopicStore()


def test_projector_hook_writes_topics_when_both_wired() -> None:
    stub = _StubLLM()
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "Theme",
                    "summary": "content",
                    "keywords": [],
                    "confidence": 0.9,
                    "supporting_chunk_ids": ["s1"],
                }
            ]
        }
    )
    extractor = TopicExtractor(llm=stub, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_for_document(version.document_id)
    assert [t.label for t in items] == ["Theme"]


def test_projector_hook_skipped_when_store_missing() -> None:
    stub = _StubLLM()
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    _run_projector(extractor=extractor, store=None, version=version)
    # No stub call — the hook never fired because the store was None.
    assert stub.calls == []


def test_projector_with_no_setters_does_not_extract() -> None:
    version = _make_version()
    store = InMemoryDocumentTopicStore()
    _run_projector(extractor=None, store=store, version=version)
    items, _ = store.list_all()
    assert items == []


def test_projector_hook_replaces_topics_on_re_projection() -> None:
    stub = _StubLLM()
    # First projection.
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "Old",
                    "summary": "v1",
                    "keywords": [],
                    "confidence": 0.8,
                    "supporting_chunk_ids": ["s1"],
                }
            ]
        }
    )
    # Second projection — replaces the prior batch atomically.
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "New",
                    "summary": "v2",
                    "keywords": [],
                    "confidence": 0.9,
                    "supporting_chunk_ids": ["s1"],
                }
            ]
        }
    )
    extractor = TopicExtractor(llm=stub, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    _run_projector(extractor=extractor, store=store, version=version)
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_for_document(version.document_id)
    assert [t.label for t in items] == ["New"]


def test_projector_hook_swallows_extractor_failure() -> None:
    """A bad LLM run must not roll back the structural projection."""
    stub = _StubLLM()
    stub.fail_with(RuntimeError("LLM down"))
    extractor = TopicExtractor(llm=stub, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    # Should not raise; the projector hook eats the error.
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_all()
    assert items == []


def test_projector_hook_skips_save_when_no_topics_extracted() -> None:
    stub = _StubLLM()
    stub.enqueue({"topics": []})
    extractor = TopicExtractor(llm=stub, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_all()
    assert items == []


def test_extracted_topics_are_persistable_through_save_topics() -> None:
    """End-to-end: an LLM-emitted topic round-trips through the
    in-memory store cleanly (smoke check on the
    schema-version + sentinel-extracted-at handshake)."""
    stub = _StubLLM()
    stub.enqueue(
        {
            "topics": [
                {
                    "label": "Theme",
                    "summary": "content",
                    "keywords": ["a", "b"],
                    "confidence": 0.9,
                    "supporting_chunk_ids": ["s1"],
                }
            ]
        }
    )
    extractor = TopicExtractor(llm=stub, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    store = InMemoryDocumentTopicStore()
    store.save_topics(topics)
    items, _ = store.list_for_document(version.document_id)
    assert len(items) == 1
    assert isinstance(items[0], DocumentTopic)
    # Server-stamped extracted_at overwrote the sentinel.
    assert items[0].extracted_at >= datetime(2026, 5, 11, tzinfo=UTC)
