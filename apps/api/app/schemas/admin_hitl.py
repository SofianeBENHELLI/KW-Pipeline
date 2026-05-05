"""Request/response shapes for the Admin HITL dashboard (#215).

EPIC-A close-out — surfaces the per-bucket SPC sampling counters,
the drift detector's effective sampling rate, and the pending
auto-promotion queue depth as a single read-only snapshot. Powers
``GET /admin/hitl/state`` and the ``/admin/hitl`` admin UI page.

Per ADR-023 §6 the dashboard is *read-only*: counters are monotonic
by design and the worker is triggered by the existing
``POST /admin/hitl/run_auto_promote_pass`` route. A future "vacuum"
admin tool slices in for counter resets.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel


class BucketState(BaseModel):
    """One ``(content_type, topic_cluster)`` row in the dashboard table.

    Mirrors the SPC ``sampling_state`` table the HITL router writes
    on every routing decision, plus two derived fields the dashboard
    needs to render hot-spots without recomputing client-side:

    - ``drift_ratio`` is ``samples_human_after_auto / max(samples_auto, 1)``,
      the same ratio the :class:`HITLDriftDetector` reads to decide
      whether to ramp. The route encodes the ``max(_, 1)`` so a
      cold-start bucket (no auto decisions yet) reports ``0.0``
      instead of a ``ZeroDivisionError`` and the UI can sort the
      table without special-casing.

    - ``effective_sample_rate`` is what the drift detector returns
      for this bucket *today* — the configured baseline for non-drifting
      buckets, ``min(1.0, baseline * ramp_factor)`` for buckets above
      the threshold. Letting the route compute this means the UI
      doesn't have to mirror the detector's logic and the snapshot
      stays consistent with what the router will see on the next
      decision.

    ``topic_cluster`` is the canonical SPC bucket key — the
    ``"_unknown_"`` sentinel from
    :data:`app.services.sampling_state_store.UNKNOWN_TOPIC_CLUSTER`
    surfaces verbatim so operators can see "no cluster" rows next to
    real clusters.
    """

    content_type: str = Field(
        description=(
            "MIME-style content type the SPC bucket is keyed on (e.g. "
            "``text/plain``, ``application/pdf``). Mirrors the catalog's "
            "``DocumentVersion.content_type``."
        ),
    )
    topic_cluster: str = Field(
        description=(
            'Topic-cluster id the bucket is keyed on, or ``"_unknown_"`` '
            "when no cluster was assigned. Same sentinel "
            ":class:`HITLRouter` stamps on routing decisions."
        ),
    )
    samples_taken: int = Field(
        description="Total routing decisions recorded for this bucket.",
        ge=0,
    )
    samples_auto: int = Field(
        description="Decisions where the router picked ``auto``.",
        ge=0,
    )
    samples_human: int = Field(
        description=(
            "Decisions where the router picked ``human`` (covers "
            "below-threshold, OCR-override, and SPC-escalated paths)."
        ),
        ge=0,
    )
    samples_human_after_auto: int = Field(
        description=(
            "Drift signal — bumped when a human reviewer flips a "
            "previously-auto-routed version (the ``hitl.review_service`` "
            "rejection handler writes this column)."
        ),
        ge=0,
    )
    drift_ratio: float = Field(
        description=(
            "``samples_human_after_auto / max(samples_auto, 1)``. "
            "When above ``drift_threshold`` the drift detector ramps "
            "this bucket's sampling rate. Cold-start buckets "
            "(``samples_auto == 0``) report ``0.0`` so the table sort "
            "stays well-defined."
        ),
        ge=0.0,
    )
    effective_sample_rate: float = Field(
        description=(
            "What :meth:`HITLDriftDetector.sampling_rate` returns for "
            "this bucket today: the configured baseline for non-drifting "
            "buckets, ``min(1.0, baseline * ramp_factor)`` for buckets "
            "above the drift threshold."
        ),
        ge=0.0,
        le=1.0,
    )
    last_decision_at: datetime | None = Field(
        default=None,
        description=(
            "Wall clock the last routing decision for this bucket was "
            "stamped onto the SPC counters. ``None`` for buckets that "
            "exist but never recorded a decision (a defensive case "
            "the in-memory store guards against by only inserting on "
            "decision)."
        ),
    )


class AdminHITLStateResponse(BaseModel):
    """Snapshot of the HITL routing state for the Admin dashboard.

    Read-only — the route never mutates and the counters are monotonic
    by design. The configuration block at the top mirrors the env vars
    the operator pinned at deploy time so the UI can render a
    "deployment posture" header without a second probe to
    ``/admin/config``. The bucket list is sorted by ``drift_ratio``
    DESC so the noisiest buckets surface at the top of the table.
    """

    enabled: bool = Field(
        description=(
            "Mirrors ``not KW_HITL_DISABLE_SCORER``. When ``False`` "
            "the router and auto-promoter are both unwired; this "
            "snapshot reports the env state but every bucket count is "
            "zero (the router never ran)."
        ),
    )
    force_auto_corpus: bool = Field(
        description=(
            "Mirrors ``KW_HITL_FORCE_AUTO_CORPUS`` (ADR-023 §6). When "
            "``True`` the router auto-routes every version regardless "
            "of score, OCR flag, or SPC sampling. The UI surfaces a "
            "loud warning banner in this state."
        ),
    )
    threshold: float = Field(
        description=(
            "Mirrors ``KW_HITL_AUTO_VALIDATE_THRESHOLD`` — versions "
            "with confidence ≥ this value are routed to the auto path."
        ),
        ge=0.0,
        le=1.0,
    )
    baseline_sample_rate: float = Field(
        description=(
            "Mirrors ``KW_HITL_SPC_SAMPLE_RATE`` — the cold-start "
            "fraction of versions that *would* auto-validate but are "
            "escalated to a human as a quality probe."
        ),
        ge=0.0,
        le=1.0,
    )
    drift_threshold: float = Field(
        description=(
            "Mirrors ``KW_HITL_DRIFT_THRESHOLD`` — the "
            "``samples_human_after_auto / samples_auto`` ratio above "
            "which a bucket's sampling rate ramps."
        ),
        ge=0.0,
    )
    drift_ramp_factor: float = Field(
        description=(
            "Mirrors ``KW_HITL_DRIFT_RAMP_FACTOR`` — the multiplier "
            "applied to ``baseline_sample_rate`` for drifting buckets, "
            "capped at 1.0."
        ),
        ge=0.0,
    )
    pending_auto_promotions: int = Field(
        description=(
            "Count of ``ValidationMetadata`` rows where "
            "``routing_decision == 'auto'`` AND ``validation_method "
            "IS NULL`` — the queue the auto-promotion worker would "
            "process on the next pass. The dashboard's "
            "``Run pass`` trigger calls "
            "``POST /admin/hitl/run_auto_promote_pass`` to drain it."
        ),
        ge=0,
    )
    buckets: list[BucketState] = Field(
        default_factory=list,
        description=(
            "Per-bucket SPC counters + derived drift signals, sorted "
            "by ``drift_ratio`` DESC so the noisiest buckets surface "
            "at the top. Empty when no routing decisions have been "
            "recorded yet (cold-start deployment)."
        ),
    )


__all__ = [
    "AdminHITLStateResponse",
    "BucketState",
]
