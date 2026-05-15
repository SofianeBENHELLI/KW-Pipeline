"""Grounded chat surface for the knowledge layer (Phase 3 follow-up).

The :class:`KnowledgeChatService` is the seam between ``POST /knowledge/chat``
and the existing retrieval/LLM primitives:

- :class:`KnowledgeSearchService` produces the vector-similarity hits
  that ground RAG and Hybrid modes.
- :class:`GraphStore` produces the projected entity triples that
  ground GraphRAG and Hybrid modes.
- :class:`LLMClient.complete_text` produces the natural-language
  answer from a context-augmented prompt.

ADR-013 forbids LangChain anywhere in the install graph; the mode
dispatch here is intentionally a small Python switch on three
``ChatMode`` values rather than a framework.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from app.schemas.knowledge import (
    ChatCitation,
    ChatMode,
    ChatResponse,
    GraphEdge,
    GraphNode,
)
from app.services.knowledge.graph_store import GraphStore
from app.services.knowledge.hybrid_search import HybridSearchService
from app.services.knowledge.llm_client import LLMClient
from app.services.knowledge.search import KnowledgeSearchService

log = logging.getLogger(__name__)

# System prompt is invariant across modes so the Anthropic prompt
# cache (ADR-014 §2 / `complete_text`'s ephemeral wrap) can amortise
# it. Per-question variation lives in the user message.
_SYSTEM_PROMPT = (
    "You are a careful research assistant answering questions strictly "
    "from the provided context. Follow these rules:\n"
    "1. Ground every claim in the context. Cite sources inline as "
    "[chunk_id] for chunk excerpts and [doc:document_id] for graph "
    "triples.\n"
    "2. If the context does not contain the information needed, reply "
    'with: "I don\'t have enough context to answer that."\n'
    "3. Keep answers concise — 1 to 4 sentences unless the question "
    "explicitly asks for more.\n"
    "4. Never invent chunk_ids or document_ids; only cite identifiers "
    "that appear in the context block."
)

# Default upper bound on answer length. Free-text completions are
# bounded server-side because a runaway response burns tokens for the
# operator and slows down the UX. Override via the constructor for
# longer-form chat.
DEFAULT_MAX_OUTPUT_TOKENS = 1024

# Deterministic answer returned when retrieval finds nothing. Mirrors
# the system prompt's rule 2 verbatim so the operator-facing copy is
# identical regardless of whether the LLM produced it or the
# short-circuit did. Saves one LLM round-trip on the empty path.
EMPTY_RETRIEVAL_ANSWER = "I don't have enough context to answer that."

# Citation marker patterns the validator recognises. Two shapes the
# system prompt asks the model to emit:
#
# - ``[chunk_id]`` for chunk excerpts. The validator only flags a
#   marker as a hallucination when the inner text "looks like" a chunk
#   id (alphanumerics, ``-``, ``_``) — that way prose like ``[NOTE]``
#   or ``[Section 1]`` doesn't get mistaken for a citation.
# - ``[doc:document_id]`` for graph triples. The ``doc:`` prefix is
#   namespaced enough that we always treat it as a citation candidate.
_CHUNK_CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9_-]+)\]")
_DOC_CITATION_PATTERN = re.compile(r"\[doc:([^\[\]\s]+)\]")


class KnowledgeChatService:
    """Answer a question grounded in vector hits and/or graph triples.

    Stateless beyond the injected dependencies; safe to construct once
    per ``PipelineServices`` and reuse across requests. The service
    does not own retry/circuit-breaker semantics — those live on the
    underlying :class:`LLMClient` and :class:`KnowledgeSearchService`.
    """

    def __init__(
        self,
        *,
        search: KnowledgeSearchService | HybridSearchService,
        graph_store: GraphStore,
        llm: LLMClient,
        llm_model: str,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._search = search
        self._graph_store = graph_store
        self._llm = llm
        self._llm_model = llm_model
        self._max_output_tokens = max_output_tokens

    @property
    def llm_model(self) -> str:
        return self._llm_model

    def answer(
        self,
        question: str,
        *,
        mode: ChatMode = "rag",
        top_k: int = 5,
        accessible_document_id: Callable[[str], bool] | None = None,
    ) -> ChatResponse:
        """Build the prompt, call the LLM, return a typed response.

        Empty / whitespace-only questions are rejected with
        :class:`ValueError`; the route layer maps that to a 422 with
        the public error envelope.

        ``accessible_document_id`` (EPIC-D D.5) is an optional predicate
        the route layer wires to drop chunk hits whose owning document
        the caller cannot see. When ``None`` (the default), no filter
        runs — back-compat for in-process callers and tests that don't
        care about scope. The predicate is called once per unique
        document_id seen in the hit set; results are cached for the
        duration of one ``answer`` call.
        """
        if not question or not question.strip():
            raise ValueError("question must not be empty.")
        cleaned = question.strip()

        started = time.perf_counter()
        hits = self._search.search(cleaned, limit=top_k).results

        if accessible_document_id is not None:
            access_cache: dict[str, bool] = {}

            def _cached_check(document_id: str) -> bool:
                cached = access_cache.get(document_id)
                if cached is None:
                    cached = accessible_document_id(document_id)
                    access_cache[document_id] = cached
                return cached

            hits = [hit for hit in hits if _cached_check(hit.document_id)]

        # Empty-retrieval short-circuit. ADR-016 calls this out as a
        # service-layer follow-up to the route-shape decision: when
        # vector retrieval returns zero hits, every mode (RAG / graph
        # / hybrid) ends up with an empty context block, and the
        # system prompt's rule 2 already mandates the deterministic
        # "I don't have enough context to answer that." response. We
        # short-circuit before the LLM call to save the round-trip.
        if not hits:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log.info(
                "knowledge.chat.empty_retrieval",
                extra={
                    "mode": mode,
                    "top_k": top_k,
                    "embedding_model": self._search.embedding_model,
                    "latency_ms": elapsed_ms,
                },
            )
            return ChatResponse(
                question=question,
                mode=mode,
                answer=EMPTY_RETRIEVAL_ANSWER,
                citations=[],
                embedding_model=self._search.embedding_model,
                llm_model=self._llm_model,
                token_usage={},
                warnings=[],
            )

        # Graph and hybrid modes pull the projected subgraph for every
        # document the vector search surfaced. This is the simplest
        # GraphRAG seed: "documents whose chunks are similar to the
        # question" — no entity-name matching, no NER hop. The graph
        # context is then formatted as triples.
        triples: list[tuple[str, GraphNode, GraphEdge, GraphNode]] = []
        if mode in ("graph", "hybrid"):
            triples = self._collect_triples_for_documents(
                document_ids=[hit.document_id for hit in hits],
            )

        user_prompt = self._build_user_prompt(
            question=cleaned,
            mode=mode,
            hits=hits,
            triples=triples,
        )

        answer_text, token_usage = self._llm.complete_text(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=self._max_output_tokens,
        )

        citations = [
            ChatCitation(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                version_id=hit.version_id,
                section_id=hit.section_id,
                snippet=hit.snippet,
                score=hit.score,
            )
            for hit in hits
        ]

        # Server-side citation validation. The system prompt asks the
        # model to cite chunks as ``[chunk_id]`` and documents as
        # ``[doc:document_id]``; we flag any marker that doesn't
        # resolve against the returned citations. The answer text is
        # NOT rewritten — the renderer can highlight valid citations
        # and surface ``warnings`` to the operator separately. Avoids
        # the LLM hallucinating a chunk_id and the user trusting it.
        warnings = _validate_citations(answer_text, citations)
        if warnings:
            log.warning(
                "knowledge.chat.unresolved_citation",
                extra={
                    "mode": mode,
                    "unresolved_markers": warnings,
                    "citation_chunk_ids": [c.chunk_id for c in citations],
                    "llm_model": self._llm_model,
                },
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "knowledge.chat.answered",
            extra={
                "mode": mode,
                "top_k": top_k,
                "vector_hits": len(hits),
                "graph_triples": len(triples),
                "embedding_model": self._search.embedding_model,
                "llm_model": self._llm_model,
                "input_tokens": token_usage.get("input_tokens", 0),
                "output_tokens": token_usage.get("output_tokens", 0),
                "unresolved_citation_count": len(warnings),
                "latency_ms": elapsed_ms,
            },
        )

        return ChatResponse(
            question=question,
            mode=mode,
            answer=answer_text,
            citations=citations,
            embedding_model=self._search.embedding_model,
            llm_model=self._llm_model,
            token_usage=token_usage,
            warnings=warnings,
        )

    # ─── Context helpers ───────────────────────────────────────────

    def _collect_triples_for_documents(
        self,
        *,
        document_ids: list[str],
    ) -> list[tuple[str, GraphNode, GraphEdge, GraphNode]]:
        """Return ``(document_id, subject, edge, object)`` for every entity edge.

        Walks every projected subgraph for the seed documents and
        keeps only ``has_entity`` edges (Phase 2 LLM-emitted, citation-
        carrying triples). De-duplicates across documents because the
        vector search can return multiple chunks from the same family.
        """
        seen_documents: set[str] = set()
        out: list[tuple[str, GraphNode, GraphEdge, GraphNode]] = []
        for document_id in document_ids:
            if document_id in seen_documents:
                continue
            seen_documents.add(document_id)
            projection = self._graph_store.find_subgraph_for_document(document_id)
            nodes_by_id = {node.id: node for node in projection.nodes}
            for edge in projection.edges:
                if edge.kind != "has_entity":
                    continue
                subject = nodes_by_id.get(edge.source_id)
                obj = nodes_by_id.get(edge.target_id)
                if subject is None or obj is None:
                    continue
                out.append((document_id, subject, edge, obj))
        return out

    def _build_user_prompt(
        self,
        *,
        question: str,
        mode: ChatMode,
        hits: list,
        triples: list[tuple[str, GraphNode, GraphEdge, GraphNode]],
    ) -> str:
        """Format the per-question user message.

        Layout is mode-dependent but always starts with the rendered
        context block(s) and ends with the question on its own line.
        Each chunk block carries the ``chunk_id`` on its own line so
        the model can copy the identifier verbatim into citations.
        """
        sections: list[str] = []

        if mode in ("rag", "hybrid"):
            sections.append(_format_chunk_block(hits))
        if mode in ("graph", "hybrid"):
            sections.append(_format_triple_block(triples))

        sections.append(f"Question:\n{question}")
        return "\n\n".join(sections)


def _format_chunk_block(hits: list) -> str:
    """Render vector-search hits as a numbered chunk-context block."""
    if not hits:
        return "Chunk context:\n(no matching chunks were retrieved)"
    lines: list[str] = ["Chunk context:"]
    for index, hit in enumerate(hits, start=1):
        lines.append(f"[{index}] chunk_id={hit.chunk_id}")
        lines.append(f"    document_id={hit.document_id} version_id={hit.version_id}")
        if hit.snippet is not None and hit.snippet.strip():
            lines.append(f"    snippet={hit.snippet}")
    return "\n".join(lines)


def _format_triple_block(
    triples: list[tuple[str, GraphNode, GraphEdge, GraphNode]],
) -> str:
    """Render graph-projected entity triples as a numbered triple-context block."""
    if not triples:
        return "Graph context:\n(no projected triples were found for the retrieved documents)"
    lines: list[str] = ["Graph context:"]
    for index, (document_id, subject, edge, obj) in enumerate(triples, start=1):
        predicate = _predicate_label(edge)
        subject_label = subject.label or subject.id
        object_label = obj.label or obj.id
        lines.append(
            f"[{index}] [doc:{document_id}] {subject_label} -[{predicate}]-> {object_label}"
        )
    return "\n".join(lines)


def _validate_citations(
    answer_text: str,
    citations: list[ChatCitation],
) -> list[str]:
    """Return a list of citation markers in the answer that don't resolve.

    The system prompt asks the model to cite chunks as ``[chunk_id]``
    and documents as ``[doc:document_id]``. This validator extracts
    every such marker from the answer text and reports the ones that
    don't match a returned citation. The answer text itself is NOT
    rewritten — surfacing a list of warnings keeps the renderer in
    charge of how to display the failure.

    Heuristics:

    - ``[doc:X]`` is always treated as a citation candidate (the
      ``doc:`` prefix is namespaced). If ``X`` isn't a returned
      ``document_id``, the marker is unresolved.
    - ``[X]`` is only treated as a citation candidate when ``X`` looks
      like an id (alphanumerics, ``-``, ``_``). Prose like ``[NOTE]``
      passes the pattern but resolves cleanly when ``NOTE`` isn't a
      chunk id we returned — that's flagged. Operators see the
      warning, decide whether to tighten the prompt or accept the
      noise.

    Bracketed prose with spaces or punctuation (e.g. ``[Section 1]``,
    ``[see appendix]``) is silently ignored because the inner pattern
    won't match.
    """
    if not answer_text:
        return []
    valid_chunks = {c.chunk_id for c in citations}
    valid_docs = {c.document_id for c in citations}
    unresolved: list[str] = []
    seen: set[str] = set()

    for match in _DOC_CITATION_PATTERN.finditer(answer_text):
        marker = match.group(0)
        document_id = match.group(1)
        if document_id in valid_docs or marker in seen:
            continue
        seen.add(marker)
        unresolved.append(marker)

    # ``_CHUNK_CITATION_PATTERN`` doesn't match ``[doc:X]`` (the colon
    # isn't in ``[A-Za-z0-9_-]``) so we don't need a startswith guard.
    for match in _CHUNK_CITATION_PATTERN.finditer(answer_text):
        marker = match.group(0)
        chunk_id = match.group(1)
        if chunk_id in valid_chunks or marker in seen:
            continue
        seen.add(marker)
        unresolved.append(marker)

    return unresolved


def _predicate_label(edge: GraphEdge) -> str:
    """Pick the human-readable predicate for a graph edge.

    Phase 2 ``has_entity`` edges carry the original predicate text in
    their ``properties["predicate"]`` field; everything else falls
    back to the edge ``kind``.
    """
    properties = edge.properties or {}
    predicate = properties.get("predicate") if isinstance(properties, dict) else None
    if isinstance(predicate, str) and predicate.strip():
        return predicate.strip()
    return edge.kind


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "EMPTY_RETRIEVAL_ANSWER",
    "KnowledgeChatService",
]
