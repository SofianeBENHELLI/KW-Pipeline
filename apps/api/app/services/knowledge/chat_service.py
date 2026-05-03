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
import time

from app.schemas.knowledge import (
    ChatCitation,
    ChatMode,
    ChatResponse,
    GraphEdge,
    GraphNode,
)
from app.services.knowledge.graph_store import GraphStore
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
    "with: \"I don't have enough context to answer that.\"\n"
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
        search: KnowledgeSearchService,
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
    ) -> ChatResponse:
        """Build the prompt, call the LLM, return a typed response.

        Empty / whitespace-only questions are rejected with
        :class:`ValueError`; the route layer maps that to a 422 with
        the public error envelope.
        """
        if not question or not question.strip():
            raise ValueError("question must not be empty.")
        cleaned = question.strip()

        started = time.perf_counter()
        hits = self._search.search(cleaned, limit=top_k).results

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
                "latency_ms": elapsed_ms,
            },
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
        return ChatResponse(
            question=question,
            mode=mode,
            answer=answer_text,
            citations=citations,
            embedding_model=self._search.embedding_model,
            llm_model=self._llm_model,
            token_usage=token_usage,
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


__all__ = ["KnowledgeChatService", "DEFAULT_MAX_OUTPUT_TOKENS"]
