"""5-signal HITL confidence scorer (ADR-023, EPIC-A A.1, #215).

The :class:`ConfidenceScorer` is a **pure function**: it takes a
version + projection context and returns a
:class:`ConfidenceScore`. No side effects, no I/O — the wiring layer
owns the persistence to ``validation_metadata`` and the audit-event
emission.

Five signals, each in ``[0.0, 1.0]`` with ``1.0 = best``:

1. **OCR flag** (hard override) — versions produced via OCR force
   ``overall = 0.0`` and ``ocr_override_active = True``.
2. **Orphan chunk ratio** — chunks with no incoming/outgoing
   relations in the version's projection. Lower = better.
3. **Section length z-score** — fraction of sections whose ``|z|``
   against ``(content_type, topic_cluster)`` corpus norms is
   below ``_LENGTH_Z_THRESHOLD``.
4. **Topic incoherence ratio** — fraction of chunks whose topic id
   differs from the document's dominant topic. Lower = better.
5. **Citation coverage** — fraction of extracted entities with a
   non-empty ``source_reference_id``, or (Phase 2 off) the
   asset-count z-score against corpus norms.

Weights default to equal (``0.2`` each) and normalise to sum 1.0;
operators tune via the ``KW_HITL_WEIGHT_*`` env vars. The OCR
override is applied **after** the weighted sum: a single OCR'd
version forces a 0.0 score regardless of every other signal.

The scorer reads pre-computed chunks + relations + topic
membership from the same lane-B services the knowledge projector
already runs, so the score is consistent with the projection that
will land on the same version. ``DocumentTopicProvider``-style
indirection isn't needed because the scorer always consumes the
freshly-extracted ``SemanticDocument`` for *this* version, not a
catalog-wide topic map.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.schemas.document import DocumentVersion
from app.schemas.semantic_document import SemanticDocument
from app.schemas.validation_metadata import ConfidenceScore
from app.services.corpus_norms import (
    METRIC_ASSET_COUNT,
    METRIC_SECTION_LENGTH,
    CorpusNormsProvider,
)
from app.services.knowledge.chunk_relations import (
    ChunkRecord,
    ChunkRelation,
    ChunkRelationService,
)
from app.services.knowledge.topic_clustering import TopicClusteringService

log = logging.getLogger(__name__)

# Threshold for the section-length / asset-count z-score signals.
# A section whose |z| exceeds this is "length-anomalous" relative to
# its corpus bucket. ``2.5`` matches the 99th percentile of a normal
# distribution (~one section in a hundred is expected to fall outside
# at random); tighter thresholds (2.0) over-flag, looser (3.0)
# under-flag — module-level so tests can monkeypatch.
LENGTH_Z_THRESHOLD: Final[float] = 2.5

# Canonical signal names. Used both as dict keys on ConfidenceScore
# and as env-var suffixes (KW_HITL_WEIGHT_<NAME>). One source of
# truth so a rename never desyncs the env layer from the persisted
# row.
SIGNAL_OCR = "ocr"
SIGNAL_ORPHAN_RATIO = "orphan_ratio"
SIGNAL_LENGTH_Z = "length_z"
SIGNAL_TOPIC_INCOHERENCE = "topic_incoherence"
SIGNAL_CITATION_COVERAGE = "citation_coverage"

ALL_SIGNALS: Final[tuple[str, ...]] = (
    SIGNAL_OCR,
    SIGNAL_ORPHAN_RATIO,
    SIGNAL_LENGTH_Z,
    SIGNAL_TOPIC_INCOHERENCE,
    SIGNAL_CITATION_COVERAGE,
)


@dataclass(frozen=True)
class EntityCitationStat:
    """Phase 2 entity-extraction summary the scorer reads.

    The scorer doesn't depend on the full ``EntityExtractionResult``
    shape — only the citation coverage. Decoupling lets the wiring
    pass an empty stat (Phase 2 off) without constructing a
    placeholder ``EntityExtractionResult``, and lets tests inject a
    fixed coverage without standing up the LLM extractor.
    """

    total_entities: int
    cited_entities: int


# Type alias for the OCR-flag callback. Keeps the scorer decoupled
# from any specific persistence shape — the wiring decides whether
# to read from raw_extraction metadata, a future ``version.is_ocr``
# field, or a parser-level signal.
OCRFlagFn = Callable[[DocumentVersion], bool]


def default_ocr_flag(_version: DocumentVersion) -> bool:
    """Default OCR-flag implementation: always ``False``.

    The OCR parser path doesn't exist yet (#47); until it does, every
    version is treated as non-OCR. This default keeps the scorer
    constructable in every wiring without forcing every test to
    inject a fake.
    """
    return False


class ConfidenceScorer:
    """5-signal confidence scorer per ADR-023.

    Stateless. Construct one per :class:`PipelineServices` container
    and reuse across requests; the corpus norms provider and the
    weight config are the only collaborator references.
    """

    SCORER_VERSION: Final[str] = "v1"

    def __init__(
        self,
        *,
        weights: dict[str, float] | None = None,
        corpus_norms: CorpusNormsProvider,
        chunk_relation_service: ChunkRelationService | None = None,
        topic_clustering_service: TopicClusteringService | None = None,
        ocr_flag_fn: OCRFlagFn = default_ocr_flag,
    ) -> None:
        self._weights = _normalize_weights(weights or _equal_weights())
        self._corpus_norms = corpus_norms
        self._chunk_relations = chunk_relation_service or ChunkRelationService()
        self._topic_clustering = topic_clustering_service or TopicClusteringService()
        self._ocr_flag_fn = ocr_flag_fn

    @property
    def weights(self) -> dict[str, float]:
        """Normalised weight dict — read-only view for tests + audit."""
        return dict(self._weights)

    def score(
        self,
        *,
        version: DocumentVersion,
        semantic: SemanticDocument,
        topic_cluster: str = "",
        entity_stat: EntityCitationStat | None = None,
    ) -> ConfidenceScore:
        """Compute the 5-signal confidence score for one version.

        Parameters
        ----------
        version:
            The catalog row whose ``content_type`` keys the corpus-
            norms bucket.
        semantic:
            The freshly-generated semantic output for this version.
            Used to derive chunks, topic memberships, asset count.
        topic_cluster:
            Bucket name for the corpus-norms lookup. The wiring
            typically passes the version's dominant topic cluster id;
            empty string falls back to a global bucket. Defaults to
            empty so the scorer is callable in unit tests without a
            topic provider.
        entity_stat:
            Optional Phase 2 entity-extraction summary. ``None``
            (Phase 2 off) triggers the asset-count fallback for the
            citation coverage signal per ADR-023 §1.
        """
        ocr_flag = self._ocr_flag_fn(version)

        chunks = self._chunk_relations.chunks_for(semantic)
        relations = self._chunk_relations.relations_for(chunks)
        assignment = self._topic_clustering.cluster(chunks, relations)
        chunk_to_topic = {m.chunk_id: m.topic_id for m in assignment.memberships}

        signals: dict[str, float] = {
            SIGNAL_OCR: 0.0 if ocr_flag else 1.0,
            SIGNAL_ORPHAN_RATIO: _orphan_signal(chunks=chunks, relations=relations),
            SIGNAL_LENGTH_Z: _length_z_signal(
                semantic=semantic,
                version=version,
                topic_cluster=topic_cluster,
                norms=self._corpus_norms,
            ),
            SIGNAL_TOPIC_INCOHERENCE: _topic_incoherence_signal(
                chunks=chunks,
                chunk_to_topic=chunk_to_topic,
            ),
            SIGNAL_CITATION_COVERAGE: _citation_coverage_signal(
                semantic=semantic,
                version=version,
                topic_cluster=topic_cluster,
                entity_stat=entity_stat,
                norms=self._corpus_norms,
            ),
        }

        if ocr_flag:
            overall = 0.0
        else:
            overall = sum(self._weights[name] * signals[name] for name in ALL_SIGNALS)
            # Floating-point arithmetic over five [0, 1] values can
            # produce a result like 0.9999999999998 or 1.0000000000002;
            # clamp so the persisted value always lies in [0, 1].
            overall = max(0.0, min(1.0, overall))

        return ConfidenceScore(
            overall=overall,
            signals=signals,
            weights=dict(self._weights),
            ocr_override_active=ocr_flag,
            computed_at=datetime.now(UTC),
            computed_by_version=self.SCORER_VERSION,
        )


# ---------------------------------------------------------------------------
# Per-signal computations (module-private; tested via the scorer surface)
# ---------------------------------------------------------------------------


def _orphan_signal(
    *,
    chunks: Sequence[ChunkRecord],
    relations: Sequence[ChunkRelation],
) -> float:
    """1.0 - (orphan chunks / total chunks). Empty doc scores 1.0."""
    if not chunks:
        return 1.0
    connected: set[str] = set()
    for relation in relations:
        connected.add(relation.source_chunk_id)
        connected.add(relation.target_chunk_id)
    orphan_count = sum(1 for chunk in chunks if chunk.chunk_id not in connected)
    return 1.0 - (orphan_count / len(chunks))


def _length_z_signal(
    *,
    semantic: SemanticDocument,
    version: DocumentVersion,
    topic_cluster: str,
    norms: CorpusNormsProvider,
) -> float:
    """Fraction of sections whose |z| ≤ LENGTH_Z_THRESHOLD."""
    sections = semantic.sections
    if not sections:
        return 1.0
    norm = norms.get(
        content_type=version.content_type,
        topic_cluster=topic_cluster,
        metric_name=METRIC_SECTION_LENGTH,
    )
    if norm is None or norm.stddev == 0.0:
        # Cold-start tolerance — unknown bucket scores 1.0 per
        # ADR-023 §1. ``stddev == 0`` (every sample identical) also
        # collapses to 1.0 because every section is at the mean by
        # definition.
        return 1.0
    within = 0
    for section in sections:
        length = len(section.text or "")
        z = abs(length - norm.mean) / norm.stddev
        if z <= LENGTH_Z_THRESHOLD:
            within += 1
    return within / len(sections)


def _topic_incoherence_signal(
    *,
    chunks: Sequence[ChunkRecord],
    chunk_to_topic: dict[str, str],
) -> float:
    """1.0 - (chunks not in dominant topic / chunks with a topic).

    Excludes chunks with no topic membership from the denominator —
    they're already counted by the orphan signal. A document where
    no chunk has a topic at all (every chunk is a singleton) scores
    1.0 here; the orphan signal absorbs the penalty.
    """
    if not chunks or not chunk_to_topic:
        return 1.0
    topic_counts: Counter[str] = Counter(chunk_to_topic.values())
    dominant_topic, _ = topic_counts.most_common(1)[0]
    chunks_with_topic = sum(1 for c in chunks if c.chunk_id in chunk_to_topic)
    if chunks_with_topic == 0:
        # Defensive: ``chunk_to_topic`` carries at least one entry but
        # none of those chunk ids appears in ``chunks``. The orphan
        # signal already penalises this shape; nothing to report here.
        return 1.0  # pragma: no cover - defensive
    incoherent = sum(
        1
        for c in chunks
        if c.chunk_id in chunk_to_topic and chunk_to_topic[c.chunk_id] != dominant_topic
    )
    return 1.0 - (incoherent / chunks_with_topic)


def _citation_coverage_signal(
    *,
    semantic: SemanticDocument,
    version: DocumentVersion,
    topic_cluster: str,
    entity_stat: EntityCitationStat | None,
    norms: CorpusNormsProvider,
) -> float:
    """Phase 2 path: fraction of cited entities. Otherwise: asset z."""
    if entity_stat is not None and entity_stat.total_entities > 0:
        return entity_stat.cited_entities / entity_stat.total_entities

    # Fallback: asset-count z-score against corpus norms.
    asset_count = len(semantic.assets)
    norm = norms.get(
        content_type=version.content_type,
        topic_cluster=topic_cluster,
        metric_name=METRIC_ASSET_COUNT,
    )
    if norm is None or norm.stddev == 0.0:
        # Cold-start: unknown bucket scores 1.0 per ADR-023 §1.
        return 1.0
    z = abs(asset_count - norm.mean) / norm.stddev
    return 1.0 if z <= LENGTH_Z_THRESHOLD else 0.0


# ---------------------------------------------------------------------------
# Weight handling
# ---------------------------------------------------------------------------


def _equal_weights() -> dict[str, float]:
    """Default weight dict — 0.2 per signal."""
    return dict.fromkeys(ALL_SIGNALS, 0.2)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Validate + normalise so the weights sum to 1.0.

    Raises:
        ValueError: when a required signal is missing, when any weight
            is negative, or when the total is non-positive (zero or NaN).

    Negative weights and an all-zero map are operator misconfigurations
    we refuse to start with — silently flattening them would hide the
    misconfiguration and produce a meaningless score.
    """
    missing = [name for name in ALL_SIGNALS if name not in weights]
    if missing:
        raise ValueError(
            f"weights missing required signal(s): {sorted(missing)}; expected {list(ALL_SIGNALS)}.",
        )
    for name in ALL_SIGNALS:
        value = weights[name]
        if math.isnan(value) or value < 0.0:
            raise ValueError(
                f"weight for {name!r} must be non-negative finite; got {value!r}.",
            )
    total = sum(weights[name] for name in ALL_SIGNALS)
    if total <= 0.0 or math.isnan(total):
        raise ValueError(
            f"weights must sum to a positive value; got {total!r}.",
        )
    return {name: weights[name] / total for name in ALL_SIGNALS}


__all__ = [
    "ALL_SIGNALS",
    "ConfidenceScorer",
    "EntityCitationStat",
    "LENGTH_Z_THRESHOLD",
    "OCRFlagFn",
    "SIGNAL_CITATION_COVERAGE",
    "SIGNAL_LENGTH_Z",
    "SIGNAL_OCR",
    "SIGNAL_ORPHAN_RATIO",
    "SIGNAL_TOPIC_INCOHERENCE",
    "default_ocr_flag",
]
