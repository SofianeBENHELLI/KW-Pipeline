"""HITL routing decision tree (ADR-023 §6, EPIC-A A.2/A.3, #215).

The :class:`HITLRouter` consumes a freshly-computed
:class:`ConfidenceScore` and returns a typed
:class:`RoutingDecision` describing where the version goes next:

- ``auto``     — high-confidence path. Persisted to
  ``ValidationMetadata.routing_decision`` for the auto-promotion
  worker (next slice) to pick up.
- ``human``    — Orbital review path. Either the score was below the
  threshold, OR the OCR override fired, OR SPC sampling escalated an
  otherwise-auto-eligible version as a quality probe.
- ``external`` — placeholder for the EPIC-B ITEROP path. The branch
  is dead today (no deployment can reach it) but recorded here so
  the audit trail's enum is stable when EPIC-B lights up.

The router does NOT transition the FSM. Auto-promotion (the actual
``mark_validated`` call on a version that was routed ``auto``) is
the next slice — a worker that scans for ``routing_decision = "auto"
AND validation_method IS NULL`` rows and calls
:meth:`ReviewService.handle_validation`. Splitting decision from
action keeps the router pure-ish (one side effect: the SPC sampling
counters), which makes it cheap to test exhaustively, and matches
ADR-023's "scoring + decision is one boundary; FSM auto-promotion is
the second" framing.

External routing decisions stay :class:`DocumentVersion` =
``NEEDS_REVIEW`` with ``validation_method = "external"``; the
external dispatch + callback wiring is EPIC-B's slice (blocked on
Q2.5 per the roadmap). This router only records the decision so
the surface is wired the moment EPIC-B unblocks.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from app.schemas.validation_metadata import (
    ConfidenceScore,
    RoutingDecision,
    RoutingMethod,
    RoutingReason,
)
from app.services.sampling_state_store import (
    SamplingBucket,
    SamplingStateStore,
)

log = logging.getLogger(__name__)


# Type alias for the rng injection. The router takes any zero-arg
# callable returning a float in ``[0, 1)`` so tests can pin the value
# without monkeypatching the ``random`` module globally. Defaults to
# :func:`random.random` so production wiring needs no extra config.
RandomFn = Callable[[], float]


@dataclass(frozen=True)
class _RouterConfig:
    """Slice the router needs from :class:`Settings` + collaborators."""

    threshold: float
    force_auto_corpus: bool
    external_workflow_enabled: bool
    sampling_rate: float


class HITLRouter:
    """Decide auto / human / external routing per ADR-023.

    Reads the :class:`ConfidenceScore` the scorer just persisted,
    applies the threshold + the global force-auto override + the SPC
    sampling state, and returns a :class:`RoutingDecision`. Does NOT
    transition the FSM and does NOT dispatch to external systems —
    that's the next slice's worker.

    The constructor accepts the threshold, the force-auto override,
    and the external-workflow flag explicitly rather than reading
    :class:`Settings` directly so the wiring layer stays the single
    place that resolves env vars (matches the discipline in
    :class:`ConfidenceScorer`). The ``external_workflow_enabled``
    flag is the EPIC-B placeholder — it's wired to ``False`` today
    by ``build_services``; once EPIC-B lands, the flag flips to
    ``settings.iterop_enabled and bool(settings.iterop_base_url)``
    and the router's ``external`` branch lights up without further
    code changes here.
    """

    ROUTER_VERSION: Final[str] = "v1"

    def __init__(
        self,
        *,
        sampling_state: SamplingStateStore,
        threshold: float,
        force_auto_corpus: bool,
        external_workflow_enabled: bool,
        sampling_rate: float = 0.05,
        random_fn: RandomFn = random.random,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(
                f"threshold must lie in [0.0, 1.0]; got {threshold!r}.",
            )
        if not 0.0 <= sampling_rate <= 1.0:
            raise ValueError(
                f"sampling_rate must lie in [0.0, 1.0]; got {sampling_rate!r}.",
            )
        self._sampling_state = sampling_state
        self._config = _RouterConfig(
            threshold=threshold,
            force_auto_corpus=force_auto_corpus,
            external_workflow_enabled=external_workflow_enabled,
            sampling_rate=sampling_rate,
        )
        self._random_fn = random_fn
        if force_auto_corpus:
            # ADR-023 §6 admin mode is a load-bearing override that
            # bypasses every other safety. We log loudly at construction
            # so accidental production use is visible from a single
            # ``grep`` over the boot log.
            log.warning(
                "hitl.force_auto_corpus_active",
                extra={
                    "threshold": threshold,
                    "sampling_rate": sampling_rate,
                    "router_version": self.ROUTER_VERSION,
                },
            )

    @property
    def threshold(self) -> float:
        """The currently-configured auto-validate threshold."""
        return self._config.threshold

    @property
    def sampling_rate(self) -> float:
        """The currently-configured baseline SPC sampling rate."""
        return self._config.sampling_rate

    def decide(
        self,
        *,
        score: ConfidenceScore,
        content_type: str,
        topic_cluster: str | None,
    ) -> RoutingDecision:
        """Return the routing decision for one freshly-scored version.

        The decision tree (ADR-023 §6, in evaluation order):

        1. If ``score.ocr_override_active`` → ``human`` (reason
           ``ocr_override``). The OCR override is a hard stop that
           bypasses every other rule, including the corpus-wide
           force-auto admin override — OCR'd content is never trusted
           regardless of admin posture.
        2. Else if ``force_auto_corpus`` → ``auto`` (reason
           ``force_auto``). This is the corpus-replay / backfill
           override; it bypasses the threshold and the SPC sampler.
        3. Else if ``external_workflow_enabled`` → ``external``
           (reason ``external_workflow``). Dead today (the wiring
           passes ``False`` until EPIC-B lights it up); documented so
           the audit enum is stable.
        4. Else if ``score.overall >= threshold``:
           - 4a. Roll a uniform ``[0, 1)`` against
             ``sampling_rate``. If the roll lands inside the rate,
             escalate to ``human`` (reason ``spc_sampled``); else
             ``auto`` (reason ``above_threshold``).
        5. Else ``human`` (reason ``below_threshold``).

        The only side effect is bumping the SPC counters via
        :class:`SamplingStateStore` — a counter that's a strict
        function of the inputs, so the same call twice with the same
        inputs deterministically advances the count by 2.
        """
        bucket = SamplingBucket.from_optional(
            content_type=content_type,
            topic_cluster=topic_cluster,
        )
        decision = self._decide_inner(score=score, bucket=bucket)
        # Side-effect: bump SPC counters. Done after the decision so a
        # bug in the decision logic surfaces as a wrong RoutingDecision
        # rather than a desynced counter.
        self._sampling_state.record_decision(
            bucket=bucket,
            method=decision.method,
        )
        return decision

    def _decide_inner(
        self,
        *,
        score: ConfidenceScore,
        bucket: SamplingBucket,
    ) -> RoutingDecision:
        """Pure decision-tree evaluation. Tests pin this directly."""
        # 1. OCR override beats everything else.
        if score.ocr_override_active:
            return self._decision(
                method="human",
                reason="ocr_override",
                score=score,
                bucket=bucket,
            )

        # 2. Corpus-wide force-auto admin override.
        if self._config.force_auto_corpus:
            return self._decision(
                method="auto",
                reason="force_auto",
                score=score,
                bucket=bucket,
            )

        # 3. External workflow (EPIC-B placeholder, dead today).
        if self._config.external_workflow_enabled:
            return self._decision(
                method="external",
                reason="external_workflow",
                score=score,
                bucket=bucket,
            )

        # 4. Threshold path. >= threshold → auto unless SPC sampled.
        if score.overall >= self._config.threshold:
            if self._spc_escalate():
                return self._decision(
                    method="human",
                    reason="spc_sampled",
                    score=score,
                    bucket=bucket,
                )
            return self._decision(
                method="auto",
                reason="above_threshold",
                score=score,
                bucket=bucket,
            )

        # 5. Below threshold → human review.
        return self._decision(
            method="human",
            reason="below_threshold",
            score=score,
            bucket=bucket,
        )

    def _spc_escalate(self) -> bool:
        """Roll the SPC sampler. Returns True iff the version escalates.

        ``sampling_rate == 0.0`` short-circuits to never escalate,
        which keeps the production "I've turned SPC off entirely"
        posture cheap (no rng call). Otherwise we draw a uniform
        ``[0, 1)`` and escalate iff the draw lies inside ``[0, rate)``.
        """
        rate = self._config.sampling_rate
        if rate <= 0.0:
            return False
        return self._random_fn() < rate

    def _decision(
        self,
        *,
        method: RoutingMethod,
        reason: RoutingReason,
        score: ConfidenceScore,
        bucket: SamplingBucket,
    ) -> RoutingDecision:
        """Build the typed :class:`RoutingDecision` payload."""
        return RoutingDecision(
            method=method,
            reason=reason,
            score_overall=score.overall,
            threshold=self._config.threshold,
            bucket=(bucket.content_type, bucket.topic_cluster),
        )


__all__ = ["HITLRouter"]
