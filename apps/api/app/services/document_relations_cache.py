"""Cached aggregate-relation read service (#380, ADR-031).

Wraps :class:`DocumentRelationsStore` (the SQLite cache) and
:class:`KnowledgeRelationsService` (the on-demand Neo4j compute)
into a single read surface the route consumes:

* ``get_or_compute(...)`` — cache hit serves the row; cache miss
  triggers the on-demand compute, writes the result, returns it.
* ``refresh(...)`` — explicit recompute, used by the route's
  ``?refresh=true`` query parameter and by the projection-completion
  hook.

The cache stores **unbounded** ``top_contributing_pairs`` as written
by the on-demand service (today max 100 per the route's ``top_n``
ceiling). On read, the route truncates to the caller's requested
``top_n``.
"""

from __future__ import annotations

import logging

from app.schemas.knowledge_relations import AggregatedRelationEvidence
from app.services.document_relations_store import DocumentRelationsStore
from app.services.knowledge.relations import (
    KnowledgeRelationsService,
    RelationNotFound,
)

log = logging.getLogger(__name__)


# Top-N ceiling used when populating the cache. The route's own
# ``top_n`` query parameter is capped at 100 (see
# ``app.routes.knowledge.explain_aggregate_relation``); we cache at
# the same ceiling so cache hits can satisfy any in-bounds caller.
_CACHE_TOP_N = 100


class DocumentRelationsCache:
    """Read façade that prefers the SQLite cache and falls through to
    the on-demand compute on miss (or when ``refresh=True``)."""

    def __init__(
        self,
        *,
        store: DocumentRelationsStore,
        relations: KnowledgeRelationsService,
    ) -> None:
        self._store = store
        self._relations = relations

    def get_or_compute(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
        top_n: int = 10,
        refresh: bool = False,
    ) -> AggregatedRelationEvidence:
        """Return the aggregate for (source, target).

        * ``refresh=True`` always recomputes via Neo4j and writes the
          result through the cache.
        * Otherwise, attempt the cache first. On hit, truncate
          ``top_contributing_pairs`` to ``top_n`` and return.
        * On miss, recompute via the on-demand path and write
          through.

        Raises :class:`RelationNotFound` when neither the cache nor
        the on-demand compute can produce evidence — same contract
        as the underlying ``KnowledgeRelationsService.explain_aggregate``.
        """
        if not refresh:
            cached = self._store.get(
                source_document_id=source_document_id,
                target_document_id=target_document_id,
            )
            if cached is not None:
                evidence, _computed_at = cached
                return _truncate(evidence, top_n=top_n)
        return self.refresh(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            top_n=top_n,
        )

    def refresh(
        self,
        *,
        source_document_id: str,
        target_document_id: str,
        top_n: int = _CACHE_TOP_N,
    ) -> AggregatedRelationEvidence:
        """Force a recompute and write-through, then return the row.

        ``top_n`` controls what the caller receives; we always cache
        at the ``_CACHE_TOP_N`` ceiling so subsequent cache hits can
        satisfy any reasonable caller without a second compute.
        """
        # Cache the full ceiling regardless of caller's top_n so a
        # follow-up "give me top 100" doesn't force a recompute.
        full_evidence = self._relations.explain_aggregate(
            source_document_id=source_document_id,
            target_document_id=target_document_id,
            top_n=_CACHE_TOP_N,
        )
        self._store.upsert(full_evidence)
        log.info(
            "knowledge.document_relations.cache.upsert",
            extra={
                "source_document_id": source_document_id,
                "target_document_id": target_document_id,
                "pair_count": full_evidence.pair_count,
                "aggregate_score": full_evidence.aggregate_score,
            },
        )
        return _truncate(full_evidence, top_n=top_n)

    def warm_for_document(self, document_id: str) -> int:
        """Recompute every cache row that names ``document_id`` as
        either side, then write both directions for each bridged pair
        (#385).

        Used by the projection-completion hook: when a document's
        knowledge subgraph is re-projected, every cached aggregate
        touching it could be stale. Walking the graph store's
        boundary-docs query returns the up-to-date set; we recompute
        and upsert each pair (both directions, since the cache
        stores them independently).

        Returns the number of pair-rows written. Pairs that no
        longer have any boundary edges (the projection removed the
        bridge) are skipped via the underlying ``RelationNotFound``
        — those rows stay stale until a future cache eviction; a
        future cleanup hook can prune them. Errors on individual
        pairs are swallowed so one malformed pair can't take down
        the whole warm pass.
        """
        rows_written = 0
        try:
            bridged = self._relations.list_bridged_documents(document_id=document_id)
        except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
            log.warning(
                "knowledge.document_relations.cache.warm.bridged_failed",
                extra={"document_id": document_id, "error_type": type(exc).__name__},
            )
            return 0
        for other in bridged:
            for src, tgt in (
                (document_id, other),
                (other, document_id),
            ):
                try:
                    self.refresh(source_document_id=src, target_document_id=tgt)
                    rows_written += 1
                except RelationNotFound:
                    # Race: the bridged set reflects a graph snapshot
                    # taken inside list_bridged_documents; by the time
                    # we recompute, the boundary edges may be gone.
                    # Skip — the next projection catches up.
                    continue
                except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
                    log.warning(
                        "knowledge.document_relations.cache.warm.pair_failed",
                        extra={
                            "source_document_id": src,
                            "target_document_id": tgt,
                            "error_type": type(exc).__name__,
                        },
                    )
                    continue
        log.info(
            "knowledge.document_relations.cache.warm",
            extra={
                "document_id": document_id,
                "bridged_doc_count": len(bridged),
                "rows_written": rows_written,
            },
        )
        return rows_written


def _truncate(
    evidence: AggregatedRelationEvidence,
    *,
    top_n: int,
) -> AggregatedRelationEvidence:
    """Return a copy with ``top_contributing_pairs`` truncated to
    ``top_n``. ``pair_count`` is preserved (it carries the
    un-truncated total for the frontend's "+ N more" indicator)."""
    if len(evidence.top_contributing_pairs) <= top_n:
        return evidence
    return evidence.model_copy(
        update={"top_contributing_pairs": evidence.top_contributing_pairs[:top_n]}
    )


__all__ = ["DocumentRelationsCache"]
