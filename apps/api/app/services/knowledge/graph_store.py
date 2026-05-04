"""Graph store boundary for the knowledge layer (ADR-012, ADR-015).

The ``GraphStore`` Protocol is the only seam between the rest of the
codebase and a concrete graph backend. Two implementations live in
this module:

- :class:`InMemoryGraphStore` is deterministic and zero-dependency;
  unit tests use it exclusively so the default ``pytest`` invocation
  does not require Docker or a Neo4j connection.
- :class:`Neo4jGraphStore` is the production implementation. It
  lazy-imports the ``neo4j`` driver so that environments without
  ``neo4j`` installed can still load this module — they just cannot
  *construct* the Neo4j store. Cypher patterns (MERGE-by-id,
  deadlock retry) are adapted from
  ``neo4j-labs/llm-graph-builder/backend/src/graphDB_dataAccess.py``
  and ``make_relationships.py`` (Apache-2.0).

Phase 3 (#186, ADR-015) adds vector-index primitives that live behind
the same Protocol so the search service never reaches into a backend:

- :meth:`GraphStore.ensure_vector_index` provisions an HNSW vector
  index on ``(:Chunk {embedding})`` (Neo4j) or a no-op (in-memory).
- :meth:`GraphStore.set_chunk_embedding` writes the per-chunk vector
  outside the wire-shape ``properties`` map so 1k-float arrays don't
  travel back through ``KnowledgeGraphProjection`` responses.
- :meth:`GraphStore.find_chunks_by_similarity` runs cosine retrieval
  and returns ranked chunks (id + locator metadata + score), the
  retrieval primitive ``KnowledgeSearchService`` consumes.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.schemas.knowledge import (
    GraphEdge,
    GraphNode,
    KnowledgeGraphPage,
    KnowledgeGraphProjection,
)

log = logging.getLogger(__name__)

# Page-size guardrails for ``find_subgraph``. Mirrors the catalog list
# route's bounds to avoid surprises.
DEFAULT_GRAPH_PAGE_LIMIT = 50
MAX_GRAPH_PAGE_LIMIT = 200

# Phase 3 vector-index name. Single canonical name across stores so
# operators and the search route don't need to know the storage layer.
VECTOR_INDEX_NAME = "chunk_embedding"

# Vector-search guardrails. Match Neo4j's HNSW typical ``ef`` budget.
DEFAULT_VECTOR_SEARCH_LIMIT = 10
MAX_VECTOR_SEARCH_LIMIT = 50


@dataclass(frozen=True)
class ChunkSearchHit:
    """One result from :meth:`GraphStore.find_chunks_by_similarity`.

    The store returns the lowest-level locator metadata it has on the
    chunk plus the cosine similarity score. The HTTP response shape
    (:class:`app.schemas.knowledge.ChunkSearchResult`) is built on top
    of this in :class:`KnowledgeSearchService`.
    """

    chunk_id: str
    document_id: str
    version_id: str
    section_id: str
    snippet: str | None
    score: float


@runtime_checkable
class GraphStore(Protocol):
    """Backend-agnostic operations for the knowledge graph projection.

    Implementations must be safe to call repeatedly with the same
    arguments — every mutating method is an upsert/merge. Read methods
    return ``KnowledgeGraphProjection`` / ``KnowledgeGraphPage`` so the
    HTTP layer can hand them straight to FastAPI without translation.
    """

    name: str

    def upsert_nodes(self, nodes: Iterable[GraphNode]) -> None:
        """Idempotently insert or update a batch of nodes."""

    def upsert_edges(self, edges: Iterable[GraphEdge]) -> None:
        """Idempotently insert or update a batch of edges."""

    def delete_subgraph_for_version(self, *, document_id: str, version_id: str) -> None:
        """Remove all nodes and edges projected for a single version.

        Phase 1 calls this before re-projecting so a re-run does not
        leave orphan section nodes from a prior projection of the
        same version.
        """

    def find_subgraph_for_document(self, document_id: str) -> KnowledgeGraphProjection:
        """Return the projected subgraph for one document family.

        Empty ``nodes``/``edges`` is a valid response — it means no
        version of this document has been validated yet (or the
        knowledge layer is disabled).
        """

    def find_subgraph(
        self, *, limit: int = DEFAULT_GRAPH_PAGE_LIMIT, cursor: str | None = None
    ) -> KnowledgeGraphPage:
        """Return one page of the catalog-wide projection.

        ``next_cursor`` is opaque; clients pass it back unchanged to
        advance. Implementations sort by ``(document_id, version_id,
        node_id)`` so pages are stable across calls.
        """

    def find_nodes_by_kind(self, kind: str) -> list[GraphNode]:
        """Return every node currently stored with the given ``kind``.

        Used by the hybrid taxonomy route (#249) to enumerate the
        ``topic`` nodes the projector emitted from topic clustering
        — those become the ``"computed"`` half of
        ``GET /knowledge/taxonomy``. The result is sorted by node id
        for byte-stability across calls. Empty graph (or no nodes of
        that kind) is a valid response: an empty list, not an
        exception.
        """

    # ─── Phase 3 vector primitives (ADR-015) ──────────────────────────

    def ensure_vector_index(self, *, name: str, dim: int) -> None:
        """Idempotently provision the chunk-embedding vector index.

        Backends without a native vector index (the in-memory store)
        treat this as a no-op; the search method falls back to a
        brute-force cosine scan over the materialised vectors.
        """

    def set_chunk_embedding(self, *, chunk_id: str, embedding: Sequence[float]) -> None:
        """Write an embedding vector for one chunk.

        Stored outside the wire-shape ``properties`` map so the public
        ``KnowledgeGraphProjection`` payload doesn't grow by 1024
        floats per chunk. ``find_chunks_by_similarity`` is the only
        public read path for these vectors.
        """

    def find_chunks_by_similarity(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int = DEFAULT_VECTOR_SEARCH_LIMIT,
        index_name: str = VECTOR_INDEX_NAME,
    ) -> list[ChunkSearchHit]:
        """Return the top-K chunks ranked by cosine similarity.

        Empty graph (or empty index) is a valid response: an empty
        list, not an exception. ``limit`` is bounded by
        :data:`MAX_VECTOR_SEARCH_LIMIT`.
        """


# ─── In-memory implementation (used by unit tests and demos) ────────────


class InMemoryGraphStore:
    """Deterministic in-process graph store backed by Python dicts.

    Suitable for unit tests and short-lived demos. Not concurrent
    beyond a single process: a coarse lock guards mutating methods so
    tests using FastAPI's TestClient (which runs handlers in a thread
    pool) don't observe partial state.
    """

    name: str = "in-memory"

    def __init__(self) -> None:
        # Keyed by node id; the kind/label/properties live on the value.
        self._nodes: dict[str, GraphNode] = {}
        # Keyed by edge id.
        self._edges: dict[str, GraphEdge] = {}
        # Reverse index from version_id → set of node_ids that the
        # projector created for that version, so re-projection can
        # delete cleanly without scanning the entire node space.
        self._version_to_node_ids: dict[str, set[str]] = {}
        # Same for edges.
        self._version_to_edge_ids: dict[str, set[str]] = {}
        # Inverse reverse index — node_id → set of versions that own it
        # (audit #226). Phase 2 entity nodes can be shared across
        # versions; without this map ``delete_subgraph_for_version`` was
        # O(versions × node_ids) — it had to scan every version's node
        # set to decide whether a freshly-orphaned node was still
        # claimed by another version. With this map the same check is
        # O(1) per node so re-projection scales linearly in the version
        # being deleted, not in the corpus size.
        self._node_to_versions: dict[str, set[str]] = {}
        # Phase 3: chunk_id → embedding vector. Lives outside
        # ``self._nodes`` so the wire-shape projection stays unchanged
        # when the embedding write path runs.
        self._chunk_embeddings: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    def upsert_nodes(self, nodes: Iterable[GraphNode]) -> None:
        with self._lock:
            for node in nodes:
                self._nodes[node.id] = node
                version_id = _version_id_from_properties(node.properties)
                if version_id is not None:
                    self._version_to_node_ids.setdefault(version_id, set()).add(node.id)
                    # Mirror the version → nodes mapping into the inverse
                    # so reference-count cleanup at delete time is O(1).
                    self._node_to_versions.setdefault(node.id, set()).add(version_id)

    def upsert_edges(self, edges: Iterable[GraphEdge]) -> None:
        with self._lock:
            for edge in edges:
                self._edges[edge.id] = edge
                version_id = _version_id_from_properties(edge.properties)
                if version_id is not None:
                    self._version_to_edge_ids.setdefault(version_id, set()).add(edge.id)

    def delete_subgraph_for_version(self, *, document_id: str, version_id: str) -> None:
        with self._lock:
            node_ids = self._version_to_node_ids.pop(version_id, set())
            edge_ids = self._version_to_edge_ids.pop(version_id, set())
            # Reference-counted node cleanup: an entity node may be
            # shared across versions (Phase 2 hashes entity ids by
            # ``(subject, subject_type)`` so the same canonical entity
            # is one node). Only remove the node when no remaining
            # version still claims it. The ``_node_to_versions`` reverse
            # index makes this an O(1) per-node check (audit #226).
            for node_id in node_ids:
                owners = self._node_to_versions.get(node_id)
                if owners is None:
                    continue
                owners.discard(version_id)
                if owners:
                    continue
                # No version still claims this node — actually remove it.
                self._node_to_versions.pop(node_id, None)
                self._nodes.pop(node_id, None)
                # Drop the chunk's embedding alongside the node so a
                # re-projection re-embeds rather than serving a stale
                # vector.
                self._chunk_embeddings.pop(node_id, None)
            for edge_id in edge_ids:
                self._edges.pop(edge_id, None)

    def find_subgraph_for_document(self, document_id: str) -> KnowledgeGraphProjection:
        with self._lock:
            nodes = sorted(
                (
                    n
                    for n in self._nodes.values()
                    if _document_id_from_properties(n.properties) == document_id
                    or n.id == document_id
                ),
                key=lambda n: (n.kind, n.id),
            )
            node_id_set = {n.id for n in nodes}
            edges = sorted(
                (
                    e
                    for e in self._edges.values()
                    if e.source_id in node_id_set and e.target_id in node_id_set
                ),
                key=lambda e: (e.kind, e.id),
            )
            # The projection is keyed by document_id; pick the most
            # recently projected version_id for the response shape (in
            # the in-memory case, "most recent" is just the lexically
            # max — production callers don't observe this since a
            # document family typically projects one version at a time
            # in deterministic order).
            version_id = ""
            for n in nodes:
                if n.kind == "version":
                    version_id = max(version_id, n.id)
            return KnowledgeGraphProjection(
                document_id=document_id,
                version_id=version_id,
                nodes=nodes,
                edges=edges,
            )

    def find_subgraph(
        self, *, limit: int = DEFAULT_GRAPH_PAGE_LIMIT, cursor: str | None = None
    ) -> KnowledgeGraphPage:
        if limit < 1 or limit > MAX_GRAPH_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_GRAPH_PAGE_LIMIT}; got {limit}.")
        with self._lock:
            all_nodes = sorted(self._nodes.values(), key=lambda n: (n.kind, n.id))
            decoded_cursor = _decode_cursor(cursor) if cursor is not None else None
            start_index = 0
            if decoded_cursor is not None:
                for i, node in enumerate(all_nodes):
                    if (node.kind, node.id) > decoded_cursor:
                        start_index = i
                        break
                else:
                    start_index = len(all_nodes)
            page = all_nodes[start_index : start_index + limit]
            page_id_set = {n.id for n in page}
            page_edges = [
                e
                for e in self._edges.values()
                if e.source_id in page_id_set or e.target_id in page_id_set
            ]
            next_cursor: str | None = None
            if len(page) == limit and start_index + limit < len(all_nodes):
                last = page[-1]
                next_cursor = _encode_cursor((last.kind, last.id))
            return KnowledgeGraphPage(
                nodes=page,
                edges=sorted(page_edges, key=lambda e: (e.kind, e.id)),
                next_cursor=next_cursor,
            )

    def find_nodes_by_kind(self, kind: str) -> list[GraphNode]:
        with self._lock:
            return sorted(
                (n for n in self._nodes.values() if n.kind == kind),
                key=lambda n: n.id,
            )

    # ─── Phase 3 vector primitives (ADR-015) ──────────────────────────

    def ensure_vector_index(self, *, name: str, dim: int) -> None:
        """No-op: the in-memory store keeps vectors in a Python dict
        and uses brute-force cosine in :meth:`find_chunks_by_similarity`.

        Accepts the same arguments as the Neo4j path so callers can
        invoke it unconditionally without branching on the store type.
        """
        if dim <= 0:
            raise ValueError(f"vector index dim must be positive; got {dim}.")

    def set_chunk_embedding(self, *, chunk_id: str, embedding: Sequence[float]) -> None:
        with self._lock:
            self._chunk_embeddings[chunk_id] = list(embedding)

    def find_chunks_by_similarity(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int = DEFAULT_VECTOR_SEARCH_LIMIT,
        index_name: str = VECTOR_INDEX_NAME,
    ) -> list[ChunkSearchHit]:
        if limit < 1 or limit > MAX_VECTOR_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_VECTOR_SEARCH_LIMIT}; got {limit}.")
        query = list(query_embedding)
        if not query:
            return []
        with self._lock:
            scored: list[tuple[float, str]] = []
            for chunk_id, vector in self._chunk_embeddings.items():
                score = _cosine_similarity(query, vector)
                if score is None:
                    continue
                scored.append((score, chunk_id))
            # Sort by descending score; tie-break on chunk_id so the
            # order is deterministic across runs.
            scored.sort(key=lambda pair: (-pair[0], pair[1]))
            hits: list[ChunkSearchHit] = []
            for score, chunk_id in scored[:limit]:
                node = self._nodes.get(chunk_id)
                if node is None:
                    continue
                props = node.properties
                hits.append(
                    ChunkSearchHit(
                        chunk_id=chunk_id,
                        document_id=str(props.get("document_id") or ""),
                        version_id=str(props.get("version_id") or ""),
                        section_id=str(props.get("section_id") or ""),
                        snippet=_string_or_none(props.get("text_preview")),
                        score=score,
                    )
                )
            return hits


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Return cosine(a, b) in ``[-1, 1]``, or ``None`` for incompatible inputs.

    Mismatched dimensionality is not raised — the search service is
    fire-and-log; a stale vector left over from a prior model is
    skipped, never crashes the request.
    """
    if len(a) != len(b):
        return None
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (norm_a * norm_b)


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _version_id_from_properties(props: Mapping[str, object]) -> str | None:
    value = props.get("version_id")
    return value if isinstance(value, str) else None


def _document_id_from_properties(props: Mapping[str, object]) -> str | None:
    value = props.get("document_id")
    return value if isinstance(value, str) else None


def _encode_cursor(value: tuple[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(value)).encode("utf-8")).decode("ascii")


def _decode_cursor(value: str) -> tuple[str, str]:
    raw = json.loads(base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8"))
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"Invalid cursor: {value!r}")
    return (str(raw[0]), str(raw[1]))


# ─── Neo4j implementation ────────────────────────────────────────────────


class Neo4jGraphStore:
    """Production graph store backed by Neo4j.

    The ``neo4j`` driver is imported lazily so this module loads in
    environments without the dependency installed (e.g. minimal CI
    images that only run unit tests). Tests targeting the real Neo4j
    behavior live behind ``pytest -m integration``.

    Cypher patterns (MERGE-by-id, deadlock retry) are adapted from
    ``neo4j-labs/llm-graph-builder/backend/src/graphDB_dataAccess.py``
    and ``make_relationships.py``, both Apache-2.0.
    """

    name: str = "neo4j"

    def __init__(
        self,
        *,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
        max_retries: int = 3,
    ) -> None:
        try:
            from neo4j import GraphDatabase  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - defensive
            raise RuntimeError(
                "Neo4jGraphStore requires the `neo4j` package. "
                "It ships in the default install; reinstall apps/api or "
                "use InMemoryGraphStore for tests."
            ) from exc

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database
        self._max_retries = max_retries

    def close(self) -> None:
        self._driver.close()

    # The ``upsert_*`` methods MERGE on (id) so calling twice with the
    # same input is a no-op; this is the contract the projector relies
    # on for safe re-projection.

    # Neo4j only accepts primitive property values (or arrays of primitives) —
    # nested dicts cannot be stored as a single property. So we *flatten*
    # ``properties`` onto the node/relationship using ``SET n += row.props``,
    # and reverse the flattening on read by treating ``id``/``kind``/``label``
    # as reserved keys and everything else as the original ``properties`` map.
    # Read paths project relationships explicitly because Bolt's Relationship
    # objects don't carry start/end node ids without an extra query.

    def upsert_nodes(self, nodes: Iterable[GraphNode]) -> None:
        rows = [
            {"id": n.id, "kind": n.kind, "label": n.label, "props": dict(n.properties)}
            for n in nodes
        ]
        if not rows:
            return
        self._write(
            """
            UNWIND $rows AS row
            MERGE (n:KnowledgeNode {id: row.id})
            SET n.kind = row.kind,
                n.label = row.label
            SET n += row.props
            """,
            {"rows": rows},
        )

    def upsert_edges(self, edges: Iterable[GraphEdge]) -> None:
        rows = [
            {
                "id": e.id,
                "kind": e.kind,
                "source_id": e.source_id,
                "target_id": e.target_id,
                "props": dict(e.properties),
            }
            for e in edges
        ]
        if not rows:
            return
        self._write(
            """
            UNWIND $rows AS row
            MATCH (s:KnowledgeNode {id: row.source_id})
            MATCH (t:KnowledgeNode {id: row.target_id})
            MERGE (s)-[r:KNOWLEDGE_EDGE {id: row.id}]->(t)
            SET r.kind = row.kind
            SET r += row.props
            """,
            {"rows": rows},
        )

    def delete_subgraph_for_version(self, *, document_id: str, version_id: str) -> None:
        # Phase 1 nodes (document/version/section) are version-scoped:
        # delete them outright. Phase 2 entity nodes are hashed by
        # (subject, subject_type) and may be referenced by other
        # versions; we delete only the HAS_ENTITY edges scoped to this
        # version, then garbage-collect entity nodes that no longer
        # have any incoming HAS_ENTITY edge.
        # Phase 1b's fix: properties are stored *flat* on the
        # node/relationship (not nested under a `properties` map), so
        # all WHERE predicates below reference the property name
        # directly (``r.version_id``, ``n.version_id``).
        self._write(
            """
            MATCH ()-[r:KNOWLEDGE_EDGE]-()
            WHERE r.version_id = $version_id
            DELETE r
            """,
            {"version_id": version_id},
        )
        self._write(
            """
            MATCH (n:KnowledgeNode)
            WHERE n.kind IN ['document','version','section']
              AND n.version_id = $version_id
            DETACH DELETE n
            """,
            {"version_id": version_id},
        )
        self._write(
            """
            MATCH (n:KnowledgeNode {kind: 'entity'})
            WHERE NOT (n)<-[:KNOWLEDGE_EDGE {kind: 'has_entity'}]-()
            DETACH DELETE n
            """,
            {},
        )

    def find_subgraph_for_document(self, document_id: str) -> KnowledgeGraphProjection:
        rows = self._read(
            """
            MATCH (n:KnowledgeNode)
            WHERE n.id = $document_id OR n.document_id = $document_id
            WITH collect(DISTINCT n) AS doc_nodes
            UNWIND doc_nodes AS n
            OPTIONAL MATCH (n)-[r:KNOWLEDGE_EDGE]-(m:KnowledgeNode)
            WHERE m IN doc_nodes
            RETURN doc_nodes AS nodes,
                   [edge IN collect(DISTINCT r) WHERE edge IS NOT NULL |
                       {id: edge.id, kind: edge.kind,
                        source_id: startNode(edge).id,
                        target_id: endNode(edge).id,
                        flat: properties(edge)}] AS edges
            """,
            {"document_id": document_id},
        )
        # Bolt ``Record`` objects return ``object`` typed values; the read
        # query above shapes ``nodes``/``edges`` as homogeneous lists, so
        # the cast to ``Any`` here matches the runtime contract.
        nodes_raw: Any
        edges_raw: Any
        nodes_raw, edges_raw = (rows[0]["nodes"], rows[0]["edges"]) if rows else ([], [])
        nodes = [_row_to_node(r) for r in nodes_raw]
        edges = [_edge_dict_to_edge(r) for r in edges_raw]
        version_id = ""
        for n in nodes:
            if n.kind == "version":
                version_id = max(version_id, n.id)
        return KnowledgeGraphProjection(
            document_id=document_id,
            version_id=version_id,
            nodes=nodes,
            edges=edges,
        )

    def find_subgraph(
        self, *, limit: int = DEFAULT_GRAPH_PAGE_LIMIT, cursor: str | None = None
    ) -> KnowledgeGraphPage:
        if limit < 1 or limit > MAX_GRAPH_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_GRAPH_PAGE_LIMIT}; got {limit}.")
        decoded = _decode_cursor(cursor) if cursor is not None else ("", "")
        rows = self._read(
            """
            MATCH (n:KnowledgeNode)
            WHERE [n.kind, n.id] > $cursor
            WITH n ORDER BY n.kind, n.id LIMIT $limit
            WITH collect(DISTINCT n) AS page_nodes
            UNWIND page_nodes AS n
            OPTIONAL MATCH (n)-[r:KNOWLEDGE_EDGE]-(m:KnowledgeNode)
            RETURN page_nodes AS nodes,
                   [edge IN collect(DISTINCT r) WHERE edge IS NOT NULL |
                       {id: edge.id, kind: edge.kind,
                        source_id: startNode(edge).id,
                        target_id: endNode(edge).id,
                        flat: properties(edge)}] AS edges
            """,
            {"cursor": list(decoded), "limit": limit},
        )
        # Bolt ``Record`` objects return ``object`` typed values; the read
        # query above shapes ``nodes``/``edges`` as homogeneous lists, so
        # the cast to ``Any`` here matches the runtime contract.
        nodes_raw: Any
        edges_raw: Any
        nodes_raw, edges_raw = (rows[0]["nodes"], rows[0]["edges"]) if rows else ([], [])
        nodes = [_row_to_node(r) for r in nodes_raw]
        edges = [_edge_dict_to_edge(r) for r in edges_raw]
        next_cursor: str | None = None
        if len(nodes) == limit:
            last = nodes[-1]
            next_cursor = _encode_cursor((last.kind, last.id))
        return KnowledgeGraphPage(nodes=nodes, edges=edges, next_cursor=next_cursor)

    def find_nodes_by_kind(  # pragma: no cover - exercised behind pytest -m integration
        self, kind: str
    ) -> list[GraphNode]:
        rows = self._read(
            """
            MATCH (n:KnowledgeNode {kind: $kind})
            RETURN n ORDER BY n.id
            """,
            {"kind": kind},
        )
        return [_row_to_node(row["n"]) for row in rows]

    # ─── Phase 3 vector primitives (ADR-015) ──────────────────────────
    # Each method below is exercised end-to-end by
    # ``tests/integration/test_neo4j_graph_store.py`` against a real
    # Neo4j 5.x with vector-index support; the default unit suite uses
    # the in-memory cosine shim. Coverage exclusion mirrors the
    # ``__init__`` and the existing Phase 1 Neo4j methods.

    def ensure_vector_index(  # pragma: no cover - exercised behind pytest -m integration
        self,
        *,
        name: str,
        dim: int,
    ) -> None:
        """Idempotently create an HNSW vector index on chunk nodes.

        Neo4j 5.13+ ``CREATE VECTOR INDEX`` syntax with ``IF NOT
        EXISTS`` keeps the call safe across restarts. Cosine
        similarity matches Voyage's embedding space.
        """
        if dim <= 0:
            raise ValueError(f"vector index dim must be positive; got {dim}.")
        self._write(
            f"""
            CREATE VECTOR INDEX {name} IF NOT EXISTS
            FOR (n:KnowledgeNode) ON n.embedding
            OPTIONS {{
                indexConfig: {{
                    `vector.dimensions`: $dim,
                    `vector.similarity_function`: 'cosine'
                }}
            }}
            """,
            {"dim": dim},
        )

    def set_chunk_embedding(  # pragma: no cover - exercised behind pytest -m integration
        self,
        *,
        chunk_id: str,
        embedding: Sequence[float],
    ) -> None:
        self._write(
            """
            MATCH (n:KnowledgeNode {id: $chunk_id, kind: 'chunk'})
            SET n.embedding = $embedding
            """,
            {"chunk_id": chunk_id, "embedding": list(embedding)},
        )

    def find_chunks_by_similarity(  # pragma: no cover - exercised behind pytest -m integration
        self,
        query_embedding: Sequence[float],
        *,
        limit: int = DEFAULT_VECTOR_SEARCH_LIMIT,
        index_name: str = VECTOR_INDEX_NAME,
    ) -> list[ChunkSearchHit]:
        if limit < 1 or limit > MAX_VECTOR_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_VECTOR_SEARCH_LIMIT}; got {limit}.")
        rows = self._read(
            """
            CALL db.index.vector.queryNodes($index_name, $limit, $vector)
            YIELD node, score
            WHERE node.kind = 'chunk'
            RETURN node.id           AS chunk_id,
                   node.document_id  AS document_id,
                   node.version_id   AS version_id,
                   node.section_id   AS section_id,
                   node.text_preview AS snippet,
                   score
            """,
            {
                "index_name": index_name,
                "limit": limit,
                "vector": list(query_embedding),
            },
        )
        # Bolt ``Record`` objects return ``object`` typed values; the
        # query above shapes each row as a flat ``dict[str, Any]`` so
        # the cast to ``Any`` here matches the runtime contract.
        hits: list[ChunkSearchHit] = []
        for row in rows:
            row_any: Any = row
            snippet_raw = row_any.get("snippet")
            hits.append(
                ChunkSearchHit(
                    chunk_id=str(row_any["chunk_id"]),
                    document_id=str(row_any.get("document_id") or ""),
                    version_id=str(row_any.get("version_id") or ""),
                    section_id=str(row_any.get("section_id") or ""),
                    snippet=snippet_raw if isinstance(snippet_raw, str) else None,
                    score=float(row_any["score"]),
                )
            )
        return hits

    # The retry pattern is adapted from llm-graph-builder's
    # ``execute_graph_query``: catch transient driver errors a few
    # times before giving up. Anything that is not a transient error
    # bubbles up immediately; the projector treats failures as
    # logged-and-skipped.
    def _write(self, cypher: str, params: dict[str, object]) -> None:
        from neo4j.exceptions import TransientError  # noqa: PLC0415

        attempt = 0
        while True:
            attempt += 1
            try:
                with self._driver.session(database=self._database) as session:
                    session.run(cypher, params)
                return
            except TransientError:
                if attempt >= self._max_retries:
                    raise
                log.warning("Neo4j transient error (attempt %d); retrying", attempt)

    def _read(self, cypher: str, params: dict[str, object]) -> list[dict[str, object]]:
        with self._driver.session(database=self._database) as session:
            return [dict(record) for record in session.run(cypher, params)]


_NODE_RESERVED_KEYS = frozenset({"id", "kind", "label"})
_EDGE_RESERVED_KEYS = frozenset({"id", "kind"})


def _row_to_node(row: Any) -> GraphNode:
    """Coerce a Neo4j Node (or dict) into our :class:`GraphNode`.

    Properties are flattened on write (see :meth:`Neo4jGraphStore.upsert_nodes`),
    so we reverse that here: ``id``/``kind``/``label`` are pulled out of the
    flat keyset and everything else becomes the original ``properties`` map.
    """
    # Bolt ``Node`` objects expose ``.items()`` but are not ``dict``; the
    # isinstance branch handles both shapes without leaking driver types up.
    raw = dict(row) if isinstance(row, dict) else dict(row.items())
    properties = {k: v for k, v in raw.items() if k not in _NODE_RESERVED_KEYS}
    return GraphNode(
        id=str(raw["id"]),
        kind=raw["kind"],
        label=str(raw["label"]),
        properties=properties,
    )


def _edge_dict_to_edge(row: Any) -> GraphEdge:
    """Coerce a projected edge dict (the shape returned by our find_subgraph
    Cypher: ``{id, kind, source_id, target_id, flat}``) into a :class:`GraphEdge`.

    ``flat`` is the relationship's full property map; the reserved keys go on
    the edge directly and the rest land back in ``properties``.
    """
    flat = dict(row.get("flat") or {})
    properties = {k: v for k, v in flat.items() if k not in _EDGE_RESERVED_KEYS}
    return GraphEdge(
        id=str(row["id"]),
        kind=row["kind"],
        source_id=str(row["source_id"]),
        target_id=str(row["target_id"]),
        properties=properties,
    )
