"""Pydantic schemas for the knowledge layer (ADR-012).

These models describe the projection of a validated ``SemanticDocument``
into a graph of ``Document``/``Version``/``Section`` nodes connected by
``PART_OF`` edges. Phase 2 (entity extraction) adds ``Entity`` nodes and
``HAS_ENTITY`` edges that carry source-reference citations.

Demo-KG (issue #140) extends the wire schema with **chunk** and
**topic** node kinds plus deterministic semantic edges
(``related_to``, ``shares_keyword``, ``same_topic_as``) and structural
edges (``has_version``, ``has_chunk``, ``belongs_to``). The new edges
land alongside ``part_of`` so existing tests keep passing.

All models inherit from :class:`APISchemaModel` so list defaults appear
as required in the serialization-mode JSON Schema (PR #107 / #80) — the
generated TypeScript on the Orbital side then sees ``T[]`` instead of
``T[] | undefined``.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

# Bump this when the wire shape of nodes/edges changes. Keep additive
# changes additive (per ADR-008): the Orbital frontend reads any v0.x
# payload, the projector writes the latest minor.
#
# v0.2 — Demo KG (#140): added chunk/topic node kinds, structural
#        has_version/has_chunk/belongs_to edges, and deterministic
#        semantic edges (related_to/shares_keyword/same_topic_as).
#        Property dict values now include ``list[str]`` so chunk and
#        topic keyword lists travel as native arrays instead of joined
#        strings. Existing v0.1 payloads remain valid.
KNOWLEDGE_GRAPH_SCHEMA_VERSION = "v0.2"

GraphNodeKind = Literal["document", "version", "section", "chunk", "topic", "entity"]
GraphEdgeKind = Literal[
    # Structural (no source_reference_id required — provenance is the
    # parent/child relationship itself).
    "part_of",
    "has_version",
    "has_chunk",
    "belongs_to",
    # Deterministic semantic (no LLM, no Anthropic key required). These
    # edges carry ``source_chunk_ids`` + ``reason`` + ``shared_keywords``
    # in their properties as an audit trail — see the contract doc at
    # docs/architecture/knowledge_graph_payload.md for the rationale.
    "related_to",
    "shares_keyword",
    "same_topic_as",
    # LLM-emitted (Phase 2). MUST carry ``source_reference_id`` from the
    # catalog's source_references table per ADR-012 §4. Triples missing
    # citations are dropped by the extractor before reaching this kind.
    "has_entity",
]

# Property values can be scalars (str/int/float/bool/None) or string
# lists. List values cover ``shared_keywords`` on chunk-relation edges
# and ``keywords`` on topic nodes — projectors emit them as native
# arrays so the typed openapi-fetch client on the frontend doesn't have
# to split on a delimiter.
GraphPropertyValue = str | int | float | bool | list[str] | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GraphNode(BaseModel):
    """One node in the knowledge graph projection.

    ``id`` is stable across projections — for ``document`` and
    ``version`` nodes it matches the catalog row ID; for ``section`` /
    ``chunk`` nodes it matches ``SemanticSection.id``; for ``topic``
    nodes it is a deterministic id from the clustering service (see
    :class:`TopicNodeProperties`); for ``entity`` nodes (Phase 2) it is
    a deterministic hash of (text, type) so re-runs converge on the
    same node.
    """

    id: str
    kind: GraphNodeKind
    label: str
    properties: dict[str, GraphPropertyValue] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """One directed edge in the knowledge graph projection.

    ``source_id`` and ``target_id`` reference :class:`GraphNode.id`
    values. Provenance requirements depend on ``kind``:

    - **structural** (``part_of``, ``has_version``, ``has_chunk``,
      ``belongs_to``) — no citation required; the edge itself is the
      provenance.
    - **deterministic semantic** (``related_to``, ``shares_keyword``,
      ``same_topic_as``) — must carry ``source_chunk_ids`` (the two
      chunks that produced the relation), ``reason`` (human-readable
      explanation), and ``shared_keywords`` in ``properties``. See
      :class:`ChunkRelationEdgeProperties`.
    - **LLM** (``has_entity``) — must carry ``source_reference_id``
      pointing at a row in the catalog's ``source_references`` table
      (ADR-012 §4). Triples missing citations are dropped by the
      extractor before edges are constructed.
    """

    id: str
    kind: GraphEdgeKind
    source_id: str
    target_id: str
    properties: dict[str, GraphPropertyValue] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Typed property contracts.
#
# These models are **documentation + construction helpers**, not the
# wire shape. ``GraphNode.properties`` and ``GraphEdge.properties``
# stay as flat dicts so the v0.1 wire payload is byte-compatible. New
# producers should build typed properties via these models and then
# call ``.model_dump()`` to flatten before assigning to a node/edge,
# e.g. ``GraphNode(..., properties=ChunkNodeProperties(...).model_dump())``.
# ---------------------------------------------------------------------------


class ChunkNodeProperties(BaseModel):
    """Stable property shape for ``kind == "chunk"`` nodes.

    A chunk is a semantic-section-derived unit consumed by the
    deterministic relation/clustering services (#141, #142). Today the
    chunk id matches ``SemanticSection.id`` 1:1; if future work splits
    a section into multiple chunks, ``section_id`` keeps the link back
    to the originating section so reviewers can navigate.
    """

    document_id: str
    version_id: str
    chunk_id: str
    section_id: str
    heading: str | None = None
    text_preview: str | None = None
    char_count: int = 0
    keywords: list[str] = Field(default_factory=list)
    topic_id: str | None = None
    source_reference_count: int = 0


class TopicNodeProperties(BaseModel):
    """Stable property shape for ``kind == "topic"`` nodes (#142).

    Topics are deterministic clusters of chunks. ``topic_id`` is stable
    across re-projections of the same input (the clustering service
    derives it from the canonical chunk-id set, not a counter), so the
    frontend can rely on it as a persistent identity.
    """

    document_id: str
    version_id: str
    topic_id: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    summary: str | None = None
    chunk_count: int = 0
    chunk_ids: list[str] = Field(default_factory=list)


class ChunkRelationEdgeProperties(BaseModel):
    """Stable property shape for deterministic chunk-relation edges
    (``related_to`` / ``shares_keyword`` / ``same_topic_as``).

    These edges come from the deterministic relation service (#141)
    and carry their own audit trail: ``source_chunk_ids`` names the
    pair, ``reason`` is the human-readable explanation rendered in the
    Orbital inspector, ``shared_keywords`` lists the overlap that
    triggered the relation, and ``score`` is the deterministic
    similarity in ``[0.0, 1.0]``.

    This is the parallel-to-ADR-012-§4 audit trail for **deterministic**
    edges (no LLM involved, so no catalog ``source_reference_id`` to
    cite — the chunks themselves are the provenance). See
    docs/architecture/knowledge_graph_payload.md for the rationale.
    """

    document_id: str
    version_id: str
    source_chunk_id: str
    target_chunk_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    shared_keywords: list[str] = Field(default_factory=list)


class TopicMembershipEdgeProperties(BaseModel):
    """Stable property shape for ``belongs_to`` edges (chunk → topic).

    The ``score`` is the clustering service's confidence that the chunk
    belongs to the topic — for hard-cluster algorithms it will always
    be ``1.0``; soft-cluster variants may emit fractional scores. The
    contract is: producers MUST set a value, consumers MUST treat
    missing ``score`` as ``1.0`` for forward compatibility.
    """

    document_id: str
    version_id: str
    chunk_id: str
    topic_id: str
    score: float = Field(default=1.0, ge=0.0, le=1.0)


class StructuralEdgeProperties(BaseModel):
    """Stable property shape for the structural edges
    (``part_of``, ``has_version``, ``has_chunk``).

    Structural edges encode the document/version/chunk skeleton and
    require no extra audit trail beyond their endpoints. The model
    exists for symmetry with the other typed property classes and so
    projectors don't have to hand-roll the shape.
    """

    document_id: str
    version_id: str
    chunk_id: str | None = None
    section_id: str | None = None


class KnowledgeGraphProjection(BaseModel):
    """Subgraph for one document family — nodes and edges that the
    projector wrote on the most recent ``VALIDATED`` transition.

    The projection is deterministic with respect to its inputs: the
    same ``Document`` + ``DocumentVersion`` + ``SemanticDocument`` will
    always produce the same nodes and edges (modulo ``generated_at``).
    Re-projecting is safe — upserts are idempotent.
    """

    schema_version: Literal["v0.1", "v0.2"] = "v0.2"
    document_id: str
    version_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utc_now)


class EntityTriple(BaseModel):
    """One ``(subject, predicate, object)`` triple emitted by the LLM.

    Phase 2 (ADR-012 §4 + ADR-013) populates the knowledge graph by
    asking the model to read a validated ``SemanticSection`` and emit
    triples with citations. The triple lands as two ``(:Entity)`` nodes
    plus a ``HAS_ENTITY``-style relation only if ``source_reference_ids``
    is non-empty — the equivalent of ADR-009's "force needs_review"
    audit gate, applied to graph edges. No edge enters the graph
    without provenance.
    """

    subject: str
    subject_type: str
    predicate: str
    object: str
    object_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_section_id: str
    # `min_length=1` enforces the "no edge without a citation" gate at
    # the schema level. Triples missing citations are appended to
    # ``EntityExtractionResult.warnings`` by the extractor instead of
    # being constructed at all.
    source_reference_ids: list[str] = Field(min_length=1)


class EntityExtractionResult(BaseModel):
    """Aggregated output of one entity-extraction pass over a version.

    Carries the validated triples plus warnings (for triples the
    extractor dropped — missing citations, citations not in the
    section's source-reference set, prompt-injection lines stripped
    from input) and per-pass token usage so the caller can log a cost
    line per validation.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    document_id: str
    version_id: str
    triples: list[EntityTriple] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=_utc_now)


class KnowledgeGraphPage(BaseModel):
    """Cursor-paginated page across all projected documents.

    Used by ``GET /knowledge/graph`` to walk the catalog's projection
    in deterministic order. ``next_cursor`` follows the same opaque
    convention as :class:`DocumentListResponse` — clients pass it
    back verbatim to advance.
    """

    schema_version: Literal["v0.1", "v0.2"] = "v0.2"
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    next_cursor: str | None = None


class ChunkSearchResult(BaseModel):
    """One match returned by ``GET /knowledge/search`` (Phase 3).

    Carries enough locator metadata for the caller to navigate back to
    the originating chunk + its document/version, plus the cosine
    similarity score. ``snippet`` is the chunk's ``text_preview`` (the
    same trimmed/200-char excerpt the projector wrote on the chunk
    node), present when the projector materialised one and ``None``
    otherwise.
    """

    chunk_id: str
    document_id: str
    version_id: str
    section_id: str
    snippet: str | None = None
    score: float = Field(ge=-1.0, le=1.0)


class ChunkSearchResponse(BaseModel):
    """Response shape for ``GET /knowledge/search`` (Phase 3, ADR-015).

    Empty ``results`` is a valid response — it means no chunk in the
    indexed set was similar enough (or no chunks have been embedded
    yet). ``query_embedding_dim`` mirrors the dimensionality the
    request was scored against, so an operator can spot
    model-mismatch errors at a glance.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    query: str
    embedding_model: str
    query_embedding_dim: int
    results: list[ChunkSearchResult] = Field(default_factory=list)


# ─── Chat (Phase 3 follow-up; grounded RAG / GraphRAG / Hybrid) ──────────

# Mode taxonomy is intentionally short. ``rag`` is the default
# vector-similarity-only path; ``graph`` substitutes graph-projected
# entity triples for the chunk excerpts; ``hybrid`` concatenates both
# contexts. Adding a new mode requires:
#
# 1. A branch in ``KnowledgeChatService._build_context``.
# 2. A regression test pinning the prompt shape it produces.
# 3. A bump to the docs in ``docs/architecture/knowledge_layer.md``.
ChatMode = Literal["rag", "graph", "hybrid"]


class ChatRequest(BaseModel):
    """Request body for ``POST /knowledge/chat`` (Phase 3 chat surface).

    ``mode`` selects the retrieval strategy used to build the grounded
    context the LLM sees. ``top_k`` bounds the number of vector hits
    retrieval will consider — the same ceiling applies to GraphRAG and
    Hybrid because both modes seed the graph traversal from the
    vector-search hits today.
    """

    question: str = Field(min_length=1, max_length=2000)
    mode: ChatMode = "rag"
    top_k: int = Field(default=5, ge=1, le=20)


class ChatCitation(BaseModel):
    """One context source the chat answer was grounded in.

    Today the only citation kind is ``chunk`` (vector-retrieval hits);
    ``graph`` mode also produces chunk citations because the graph
    traversal currently seeds from the same vector hits. A future
    GraphRAG mode that seeds from entity-name matching will introduce
    an ``entity`` citation kind.
    """

    chunk_id: str
    document_id: str
    version_id: str
    section_id: str
    snippet: str | None = None
    score: float = Field(ge=-1.0, le=1.0)


class ChatResponse(BaseModel):
    """Response body for ``POST /knowledge/chat``.

    ``answer`` is the free-text response from the LLM; ``citations``
    lists the chunks/triples the prompt grounded the model in. Empty
    ``citations`` with a non-empty ``answer`` is possible — the LLM
    answered from the question alone, which the system prompt asks it
    to flag with an explicit "no supporting context" preamble.
    """

    schema_version: Literal["v0.1"] = "v0.1"
    question: str
    mode: ChatMode
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)
    embedding_model: str | None = None
    llm_model: str
    token_usage: dict[str, int] = Field(default_factory=dict)
