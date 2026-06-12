"""High-value-chunks ranker (converged plan §C.2).

Ranks the chunks of one validated semantic document by a composite
"importance" score so a reviewer can jump straight to the dense
ones instead of paging through every section. The four signals the
plan calls out are:

1. **Claims** referencing the chunk (extracted by
   :class:`ClaimExtractor`; the chunk id appears in
   ``Claim.provenance_chunk_ids``).
2. **Process step count** referencing the chunk (SOP extractor;
   the chunk id appears in ``ProcessStep.source_reference_ids``).
3. **Graph degree** — number of chunk-relation edges incident on
   the chunk. Computed locally via
   :class:`~app.services.knowledge.chunk_relations.ChunkRelationService`
   so the ranker is self-contained (no Neo4j round-trip).
4. **Entity density** — unique entity subjects referenced by
   claims provenanced to the chunk. The current extractor stack
   doesn't materialise an entity-mention store, so we proxy with
   the count of distinct ``Claim.subject_entity_id`` values whose
   provenance includes the chunk. A future entity-mention store
   plugs in here without changing the wire shape.

The four raw counts are normalised against the document's own
per-component max (``count / max_count``) so the score is
comparable across documents of different sizes. The composite
score is a weighted sum of the normalised signals with the
default weights from the plan; the weights are configurable on
the ranker so a follow-up can A/B them against a validated corpus.

The service is **stateless** and **fire-and-log**: no DB writes,
no cross-service calls beyond reading from the injected stores.
Designed to be invoked from a route handler on every read.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final

from app.schemas.claim import Claim
from app.schemas.high_value_chunks import (
    HighValueChunk,
    HighValueChunkSignals,
)
from app.schemas.process import Process
from app.schemas.semantic_document import SemanticDocument
from app.services.knowledge.chunk_relations import ChunkRelationService

# Default weighting for the composite score. Picked from the
# converged plan §C.2 narrative ("len(claims) + len(processes_step_count)
# + degree_in_graph + entity_density"); each component gets equal
# weight as the baseline. A future tuning spike against a validated
# corpus can rebalance these without changing the wire shape.
DEFAULT_WEIGHTS: Final[HighValueChunkSignals] = HighValueChunkSignals(
    claims=0.30,
    process_steps=0.20,
    graph_degree=0.25,
    entity_density=0.25,
)

# Snippet cap so the wire stays bounded — long enough that an
# operator can recognise the content, short enough that the JSON
# payload doesn't balloon on documents with verbose sections.
_SNIPPET_CHARS: Final[int] = 240


@dataclass(frozen=True, slots=True)
class _ChunkAggregates:
    """One chunk's raw signal counts. Internal to the ranker.

    ``entity_mentions`` is the cardinality of distinct
    ``subject_entity_id`` values appearing on claims provenanced to
    this chunk. Density (per-chunk / per-document max) is computed
    by the caller once all aggregates are in.
    """

    claim_count: int
    process_step_count: int
    graph_degree: int
    entity_mention_count: int


class HighValueChunksService:
    """Score and rank a document version's chunks by importance.

    Construction is cheap; the service holds the weighting envelope
    plus an injected :class:`ChunkRelationService` (so tests can
    swap a stubbed implementation for deterministic fixtures
    without spinning up the real relation matcher).
    """

    def __init__(
        self,
        *,
        weights: HighValueChunkSignals | None = None,
        relations: ChunkRelationService | None = None,
    ) -> None:
        self._weights = weights or DEFAULT_WEIGHTS
        self._relations = relations or ChunkRelationService()

    @property
    def weights(self) -> HighValueChunkSignals:
        return self._weights

    def rank(
        self,
        *,
        semantic: SemanticDocument,
        claims: list[Claim],
        processes: list[Process],
        limit: int,
    ) -> list[HighValueChunk]:
        """Rank chunks DESC by composite importance score.

        ``claims`` and ``processes`` MUST already be scoped to the
        version under inspection — the ranker does not filter by
        ``version_id``; the store-layer call sites do (see
        ``ClaimStore.list_for_version`` / ``ProcessStore.list_for_version``).
        ``limit`` is the maximum number of rows returned. The output
        is byte-stable for a given input (ties broken by ``chunk_id``
        ASC).
        """
        sections = list(semantic.sections)
        if not sections or limit <= 0:
            return []

        # Pre-compute the four raw signals per chunk.
        aggregates = self._collect_aggregates(semantic=semantic, claims=claims, processes=processes)

        # Normalise against the document's own per-component max so
        # scores are comparable across documents.
        max_claims = max((a.claim_count for a in aggregates.values()), default=0)
        max_steps = max((a.process_step_count for a in aggregates.values()), default=0)
        max_degree = max((a.graph_degree for a in aggregates.values()), default=0)
        max_entities = max((a.entity_mention_count for a in aggregates.values()), default=0)

        rows: list[HighValueChunk] = []
        for section in sections:
            agg = aggregates.get(section.id)
            if agg is None:
                # Should not happen — _collect_aggregates seeds an
                # entry for every section. The guard keeps the
                # ranker robust against a future refactor that
                # might filter the section list before this point.
                continue
            signals = HighValueChunkSignals(
                claims=_normalise(agg.claim_count, max_claims),
                process_steps=_normalise(agg.process_step_count, max_steps),
                graph_degree=_normalise(agg.graph_degree, max_degree),
                entity_density=_normalise(agg.entity_mention_count, max_entities),
            )
            score = (
                signals.claims * self._weights.claims
                + signals.process_steps * self._weights.process_steps
                + signals.graph_degree * self._weights.graph_degree
                + signals.entity_density * self._weights.entity_density
            )
            rows.append(
                HighValueChunk(
                    chunk_id=section.id,
                    section_id=section.id,
                    heading=section.heading,
                    snippet=_snippet(section.text),
                    char_count=len(section.text or ""),
                    score=round(score, 4),
                    signals=signals,
                    claim_count=agg.claim_count,
                    process_step_count=agg.process_step_count,
                    graph_degree=agg.graph_degree,
                    entity_mention_count=agg.entity_mention_count,
                )
            )

        rows.sort(key=lambda r: (-r.score, r.chunk_id))
        return rows[:limit]

    def _collect_aggregates(
        self,
        *,
        semantic: SemanticDocument,
        claims: list[Claim],
        processes: list[Process],
    ) -> dict[str, _ChunkAggregates]:
        """Walk the three input streams once each and produce the raw
        per-chunk counts. Returns a dict keyed by section id; every
        section gets an entry so the ranker can emit zero-score
        rows for chunks that nothing references.
        """
        section_ids = {s.id for s in semantic.sections}

        claim_counts: Counter[str] = Counter()
        entity_sets: dict[str, set[str]] = {sid: set() for sid in section_ids}
        for claim in claims:
            for chunk_id in claim.provenance_chunk_ids:
                if chunk_id not in section_ids:
                    # Claim references a chunk we don't know about
                    # (e.g. stale provenance after a re-extract).
                    # Skip — it can't contribute to *this* document's
                    # ranking.
                    continue
                claim_counts[chunk_id] += 1
                if claim.subject_entity_id:
                    entity_sets[chunk_id].add(claim.subject_entity_id)
                if claim.object_entity_id:
                    entity_sets[chunk_id].add(claim.object_entity_id)

        step_counts: Counter[str] = Counter()
        for process in processes:
            for step in process.steps:
                for chunk_id in step.source_reference_ids:
                    if chunk_id not in section_ids:
                        continue
                    step_counts[chunk_id] += 1

        # Graph degree from the deterministic relation service.
        # Counts every edge end-point so each undirected edge
        # contributes ``1`` to each of its two chunks.
        chunks = self._relations.chunks_for(semantic)
        relations = self._relations.relations_for(chunks)
        degree: Counter[str] = Counter()
        for relation in relations:
            if relation.source_chunk_id in section_ids:
                degree[relation.source_chunk_id] += 1
            if relation.target_chunk_id in section_ids:
                degree[relation.target_chunk_id] += 1

        return {
            sid: _ChunkAggregates(
                claim_count=claim_counts.get(sid, 0),
                process_step_count=step_counts.get(sid, 0),
                graph_degree=degree.get(sid, 0),
                entity_mention_count=len(entity_sets.get(sid, set())),
            )
            for sid in section_ids
        }


def _normalise(value: int, maximum: int) -> float:
    """Divide ``value`` by ``maximum`` if ``maximum > 0`` else 0.0.

    The "no signal anywhere" case (every chunk has zero claims, etc)
    collapses cleanly to zero contribution from that component
    rather than emitting NaN or dividing-by-zero.
    """
    if maximum <= 0:
        return 0.0
    return value / maximum


def _snippet(text: str | None) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= _SNIPPET_CHARS:
        return flat
    return flat[: _SNIPPET_CHARS - 1].rstrip() + "…"


__all__ = [
    "DEFAULT_WEIGHTS",
    "HighValueChunksService",
]
