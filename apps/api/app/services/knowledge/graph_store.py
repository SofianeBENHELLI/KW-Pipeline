"""Graph store boundary for the knowledge layer (ADR-012).

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

The Protocol surface is intentionally small. Phase 1 only needs
upserts for the projection lifecycle plus two read shapes
(``find_subgraph_for_document`` for the per-document view and
``find_subgraph`` for the cursor-paginated catalog walk). Phase 2's
entity work will add ``upsert_entity`` and ``merge_has_entity``
methods alongside; ADR-012 explicitly carves the surface to avoid
leaking Cypher into callers.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

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
        self._lock = threading.RLock()

    def upsert_nodes(self, nodes: Iterable[GraphNode]) -> None:
        with self._lock:
            for node in nodes:
                self._nodes[node.id] = node
                version_id = _version_id_from_properties(node.properties)
                if version_id is not None:
                    self._version_to_node_ids.setdefault(version_id, set()).add(node.id)

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
            for node_id in node_ids:
                self._nodes.pop(node_id, None)
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


def _version_id_from_properties(props: dict[str, object]) -> str | None:
    value = props.get("version_id")
    return value if isinstance(value, str) else None


def _document_id_from_properties(props: dict[str, object]) -> str | None:
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
        self._write(
            """
            MATCH (n:KnowledgeNode {version_id: $version_id})
            DETACH DELETE n
            """,
            {"version_id": version_id},
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
        nodes_raw, edges_raw = (rows[0]["nodes"], rows[0]["edges"]) if rows else ([], [])
        nodes = [_row_to_node(r) for r in nodes_raw]
        edges = [_edge_dict_to_edge(r) for r in edges_raw]
        next_cursor: str | None = None
        if len(nodes) == limit:
            last = nodes[-1]
            next_cursor = _encode_cursor((last.kind, last.id))
        return KnowledgeGraphPage(nodes=nodes, edges=edges, next_cursor=next_cursor)

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


def _row_to_node(row: dict[str, object] | object) -> GraphNode:
    """Coerce a Neo4j Node (or dict) into our :class:`GraphNode`.

    Properties are flattened on write (see :meth:`Neo4jGraphStore.upsert_nodes`),
    so we reverse that here: ``id``/``kind``/``label`` are pulled out of the
    flat keyset and everything else becomes the original ``properties`` map.
    """
    raw = dict(row) if isinstance(row, dict) else dict(row.items())  # type: ignore[union-attr]
    properties = {k: v for k, v in raw.items() if k not in _NODE_RESERVED_KEYS}
    return GraphNode(
        id=str(raw["id"]),
        kind=raw["kind"],  # type: ignore[arg-type]
        label=str(raw["label"]),
        properties=properties,
    )


def _edge_dict_to_edge(row: dict[str, object]) -> GraphEdge:
    """Coerce a projected edge dict (the shape returned by our find_subgraph
    Cypher: ``{id, kind, source_id, target_id, flat}``) into a :class:`GraphEdge`.

    ``flat`` is the relationship's full property map; the reserved keys go on
    the edge directly and the rest land back in ``properties``.
    """
    flat = dict(row.get("flat") or {})  # type: ignore[arg-type]
    properties = {k: v for k, v in flat.items() if k not in _EDGE_RESERVED_KEYS}
    return GraphEdge(
        id=str(row["id"]),
        kind=row["kind"],  # type: ignore[arg-type]
        source_id=str(row["source_id"]),
        target_id=str(row["target_id"]),
        properties=properties,
    )
