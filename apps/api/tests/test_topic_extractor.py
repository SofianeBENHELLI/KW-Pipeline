"""Tests for ``TopicExtractor`` (#411, ADR-031, #438).

Mirrors the structure of ``test_claim_extractor.py``: an in-process
fake instructor client returns canned ``TopicEnvelope`` responses; we
assert on the extractor's parsing + filtering behaviour and on the
projector hook the operator workflow depends on. No network calls.

Coverage:

* Empty / whitespace-only documents skip the LLM call entirely.
* Valid responses parse into :class:`DocumentTopic` instances.
* The prompt cites every non-empty section id as allowed provenance.
* Topics with no ``supporting_chunk_ids`` are rejected by Pydantic
  before ever reaching the extractor — the LLM literally cannot emit
  a topic without provenance once instructor parses the response.
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
from pydantic import BaseModel

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
from app.services.knowledge.projector import KnowledgeProjector
from app.services.topic_extractor import TopicEnvelope, TopicExtractor, TopicWire

# ─── Test doubles ────────────────────────────────────────────────────


class _FakeUsage:
    """Stand-in for the ``completion.usage`` shape both Anthropic and
    Gemini surface. ``getattr(..., default=0)`` in the extractor
    handles missing attributes gracefully — but we populate both
    fields here so tests assert against realistic numbers when they
    care about telemetry.
    """

    def __init__(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeCompletion:
    def __init__(self, usage: _FakeUsage | None = None) -> None:
        self.usage = usage or _FakeUsage()


class _FakeInstructorClient:
    """In-process stand-in for an ``instructor.Instructor`` client.

    Tests enqueue topic dicts (the canonical wire shape) and the fake
    materialises them through :class:`TopicEnvelope` so any shape
    error surfaces here exactly the way it would surface in
    production (Pydantic raises in the real
    ``client.create_with_completion`` path too).
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
        max_tokens: int = 4096,
    ) -> tuple[Any, _FakeCompletion]:
        self.calls.append(
            {
                "response_model": response_model,
                "messages": messages,
                "max_retries": max_retries,
                "max_tokens": max_tokens,
                # Convenience accessors mirroring the legacy stub's
                # ``system`` / ``user`` keys so existing assertions
                # continue to work.
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
        # Materialise through the Pydantic model so shape errors surface
        # here just like the real instructor path (default-deny on
        # provenance via ``min_length=1`` lives on TopicWire).
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
        created_at=datetime(2026, 5, 11, tzinfo=UTC),
    )


# ─── Extractor unit tests ────────────────────────────────────────────


def test_extract_returns_parsed_topics() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue(
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
        input_tokens=1200,
        output_tokens=80,
    )
    extractor = TopicExtractor(client=fake, model="claude-sonnet-4-5")

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
    assert len(fake.calls) == 1
    # The instructor path always uses TopicEnvelope as the
    # response_model so the JSON schema is auto-generated from it.
    assert fake.calls[0]["response_model"] is TopicEnvelope


def test_extract_skips_empty_document_without_llm_call() -> None:
    fake = _FakeInstructorClient()
    extractor = TopicExtractor(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(version=version, sections=[])
    topics = extractor.extract(
        semantic,
        document=_make_document(version),
        version=version,
    )
    assert topics == []
    assert fake.calls == []


def test_extract_skips_whitespace_only_document() -> None:
    fake = _FakeInstructorClient()
    extractor = TopicExtractor(client=fake, model="m")
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
    assert fake.calls == []


def test_prompt_carries_every_non_empty_section_id_in_allowed_pool() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue({"topics": []})
    extractor = TopicExtractor(client=fake, model="m")
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
    assert len(fake.calls) == 1
    user_prompt = fake.calls[0]["user"]
    assert "Allowed supporting_chunk_ids: [s1, s3]" in user_prompt
    assert "s2" not in user_prompt.split("Allowed supporting_chunk_ids:")[1].split("\n")[0]


def test_extract_drops_topics_without_provenance() -> None:
    """A topic emitted without supporting_chunk_ids fails Pydantic
    validation (``min_length=1``) at the instructor boundary, so the
    whole envelope is invalid. In the fake we trigger the same
    rejection by emitting a malformed envelope and asserting the
    extractor swallows the failure and returns no topics — same
    behaviour as the legacy ``_parse_topics`` filter, but enforced by
    the schema rather than imperatively.
    """
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "topics": [
                {
                    "label": "missing provenance",
                    "summary": "should be rejected by Pydantic",
                    "keywords": [],
                    "confidence": 0.7,
                    "supporting_chunk_ids": [],  # violates min_length=1
                },
            ]
        }
    )
    extractor = TopicExtractor(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    # Pydantic rejected the envelope → instructor would normally
    # retry; in our fake the validation fires immediately and the
    # extractor's outer try/except catches it as a per-document
    # failure (empty list).
    assert topics == []


def test_topic_wire_rejects_empty_supporting_ids_directly() -> None:
    """Sanity check: ``TopicWire.supporting_chunk_ids`` enforces
    ``min_length=1`` so the default-deny provenance gate lives in
    the schema, not in the extractor's parser."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TopicWire(
            label="x",
            summary="y",
            confidence=0.5,
            supporting_chunk_ids=[],
        )


def test_extract_drops_topics_citing_only_unknown_section_ids() -> None:
    """Hallucinated section ids are filtered post-validation. A topic
    that cites at least one valid id is kept (with the invalid ids
    stripped); a topic that cites only invalid ids is dropped."""
    fake = _FakeInstructorClient()
    fake.enqueue(
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
    extractor = TopicExtractor(client=fake, model="m")
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
    fake = _FakeInstructorClient()
    fake.enqueue({"topics": []})
    extractor = TopicExtractor(client=fake, model="m", max_input_tokens=200)
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
    user_prompt = fake.calls[0]["user"]
    # Both sections are still in the prompt (truncation, not skip).
    assert "Section [s1]" in user_prompt
    assert "Section [s2]" in user_prompt
    # The body block was truncated — total length is bounded.
    body_start = user_prompt.index("Document body")
    body = user_prompt[body_start:]
    # Slack of one char per section + the framing "--- Section [...] ---" boilerplate.
    assert len(body) < 2000  # very loose upper bound; original was ~6000


def test_extract_token_guard_disabled_by_zero() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue({"topics": []})
    extractor = TopicExtractor(client=fake, model="m", max_input_tokens=0)
    version = _make_version()
    long_text = "alpha " * 5000
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text=long_text)],
    )
    extractor.extract(semantic, document=_make_document(version), version=version)
    user_prompt = fake.calls[0]["user"]
    # With the guard off, the full body lands in the prompt.
    assert long_text.strip() in user_prompt


def test_extract_negative_token_cap_is_rejected() -> None:
    with pytest.raises(ValueError):
        TopicExtractor(client=_FakeInstructorClient(), model="m", max_input_tokens=-1)


def test_extract_swallows_llm_failure() -> None:
    fake = _FakeInstructorClient()
    fake.fail_with(RuntimeError("LLM timed out"))
    extractor = TopicExtractor(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    # Per-document failure → empty list, no exception leaked.
    assert topics == []


def test_extract_rejects_mismatched_version() -> None:
    fake = _FakeInstructorClient()
    extractor = TopicExtractor(client=fake, model="m")
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
    fake = _FakeInstructorClient()
    fake.enqueue(
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
    extractor = TopicExtractor(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    assert len(topics) == 12


def test_extract_logs_usage_tokens_from_completion() -> None:
    """Telemetry contract: the ``knowledge.topic_extraction.completed``
    log carries usage tokens read from ``completion.usage``. Both
    Anthropic and Gemini surface the same attribute names."""
    fake = _FakeInstructorClient()
    fake.enqueue(
        {
            "topics": [
                {
                    "label": "Theme",
                    "summary": "ok",
                    "keywords": [],
                    "confidence": 0.9,
                    "supporting_chunk_ids": ["s1"],
                }
            ]
        },
        input_tokens=1234,
        output_tokens=56,
    )
    extractor = TopicExtractor(client=fake, model="m")
    version = _make_version()
    semantic = _make_semantic(
        version=version,
        sections=[SemanticSection(id="s1", heading="A", text="content")],
    )
    topics = extractor.extract(semantic, document=_make_document(version), version=version)
    # Smoke check that the path executed; the structured-log shape
    # is exercised by ``test_audit_log_handler.py`` against the real
    # logger plumbing.
    assert len(topics) == 1


# ─── Dependencies / factory ──────────────────────────────────────────


def test_maybe_build_topic_extractor_returns_none_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Strip LLM keys so the factory short-circuits to None.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    extractor = _maybe_build_topic_extractor()
    assert extractor is None


def test_maybe_build_topic_extractor_built_when_client_supplied() -> None:
    fake = _FakeInstructorClient()
    extractor = _maybe_build_topic_extractor(client=fake, model="claude-sonnet-4-5")
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
    fake = _FakeInstructorClient()
    fake.enqueue(
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
    extractor = TopicExtractor(client=fake, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_for_document(version.document_id)
    assert [t.label for t in items] == ["Theme"]


def test_projector_hook_skipped_when_store_missing() -> None:
    fake = _FakeInstructorClient()
    extractor = TopicExtractor(client=fake, model="m")
    version = _make_version()
    _run_projector(extractor=extractor, store=None, version=version)
    # No fake call — the hook never fired because the store was None.
    assert fake.calls == []


def test_projector_with_no_setters_does_not_extract() -> None:
    version = _make_version()
    store = InMemoryDocumentTopicStore()
    _run_projector(extractor=None, store=store, version=version)
    items, _ = store.list_all()
    assert items == []


def test_projector_hook_replaces_topics_on_re_projection() -> None:
    fake = _FakeInstructorClient()
    # First projection.
    fake.enqueue(
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
    fake.enqueue(
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
    extractor = TopicExtractor(client=fake, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    _run_projector(extractor=extractor, store=store, version=version)
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_for_document(version.document_id)
    assert [t.label for t in items] == ["New"]


def test_projector_hook_swallows_extractor_failure() -> None:
    """A bad LLM run must not roll back the structural projection."""
    fake = _FakeInstructorClient()
    fake.fail_with(RuntimeError("LLM down"))
    extractor = TopicExtractor(client=fake, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    # Should not raise; the projector hook eats the error.
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_all()
    assert items == []


def test_projector_hook_skips_save_when_no_topics_extracted() -> None:
    fake = _FakeInstructorClient()
    fake.enqueue({"topics": []})
    extractor = TopicExtractor(client=fake, model="m")
    store = InMemoryDocumentTopicStore()
    version = _make_version()
    _run_projector(extractor=extractor, store=store, version=version)
    items, _ = store.list_all()
    assert items == []


def test_extracted_topics_are_persistable_through_save_topics() -> None:
    """End-to-end: an LLM-emitted topic round-trips through the
    in-memory store cleanly (smoke check on the
    schema-version + sentinel-extracted-at handshake)."""
    fake = _FakeInstructorClient()
    fake.enqueue(
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
    extractor = TopicExtractor(client=fake, model="m")
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
