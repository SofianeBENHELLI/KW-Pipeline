"""HITL validation metadata schemas (ADR-023, EPIC-A A.5, #215).

These schemas describe the per-version confidence score + routing
decision produced by the smart HITL router. They are **internal**:
EPIC-A's "auto-validated == human-validated to consumers" rule means
``ValidationMetadata`` does NOT appear on the public ``Document`` /
``DocumentVersion`` API surface. Persisting them lives in
:mod:`app.services.validation_metadata_store`; the structural
contract is here so the scorer (which is pure) and the store (which
is I/O-bound) share one shape.

Schema version:

- ``ConfidenceScore.computed_by_version`` — the scorer version that
  produced the row. Bumped when the signal vocabulary changes so a
  future router slice can ignore stale scores.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas import APISchemaModel as BaseModel

# The narrow set of routing-method values written by the slice-2
# ``hitl_router.py``. Kept here so the scorer + the router share one
# source of truth and Pydantic validates the persisted value at read
# time. ``None`` is a valid persisted shape — it means the scorer ran
# but the router hasn't picked a method yet (e.g. scorer-disabled run).
RoutingMethod = Literal["auto", "human", "external"]
ValidationMethod = Literal["auto", "human", "external"]

# Reasons the router can record on a :class:`RoutingDecision`. The
# vocabulary is closed (no free-form strings) so audit queries can
# bucket decisions without a string-cleaning pass:
#
# - ``force_auto`` — ``KW_HITL_FORCE_AUTO_CORPUS`` admin override.
# - ``above_threshold`` — score >= threshold and SPC sampling did not
#   escalate.
# - ``below_threshold`` — score < threshold; routes to a human.
# - ``spc_sampled`` — score >= threshold but SPC sampling escalated to
#   human as a quality probe.
# - ``external_workflow`` — placeholder for the EPIC-B ITEROP path.
#   Dead today (no deployment can reach it) but documented so the
#   audit query has a stable enum.
# - ``ocr_override`` — OCR flag was active; routes to human regardless
#   of every other signal.
RoutingReason = Literal[
    "force_auto",
    "above_threshold",
    "below_threshold",
    "spc_sampled",
    "external_workflow",
    "ocr_override",
]


class RoutingDecision(BaseModel):
    """One routing decision per ADR-023 + the EPIC-A A.2 router slice.

    Produced by :class:`app.services.hitl_router.HITLRouter.decide` and
    audit-emitted as ``routing.decided``. ``method`` is the narrow
    routing-method literal that ``ValidationMetadata.routing_decision``
    persists; ``reason`` carries the closed enum so audit queries can
    answer "show me every version routed because of OCR override"
    without a string-cleaning pass.

    ``bucket`` is a 2-tuple ``(content_type, topic_cluster)`` so the
    SPC sampler keys per-bucket counters consistently with
    :class:`app.services.corpus_norms.CorpusNormsProvider` and the
    next-slice drift detector. ``"_unknown_"`` stands in for a missing
    ``topic_cluster`` so the bucket key is always populated.
    """

    method: RoutingMethod
    reason: RoutingReason
    score_overall: float
    threshold: float
    bucket: tuple[str, str]


class ConfidenceScore(BaseModel):
    """One scoring pass over a single version (ADR-023 §1, §4).

    ``signals`` and ``weights`` are dicts keyed by the canonical
    signal names from :mod:`app.services.confidence_scorer` so the
    on-the-wire and at-rest shapes match the live config without a
    translation table. ``ocr_override_active`` carries the hard
    override bit independently of ``overall`` so an audit query
    "show me every version where OCR forced the score to 0" doesn't
    need to compare floats.
    """

    overall: float
    signals: dict[str, float]
    weights: dict[str, float]
    ocr_override_active: bool
    computed_at: datetime
    computed_by_version: str


class ValidationMetadata(BaseModel):
    """Sidecar metadata row for one document version (ADR-023 §4).

    Stored in the ``validation_metadata`` table (migration 0007),
    keyed by ``version_id``. Every field except ``version_id`` is
    optional: the scorer fills ``confidence_score`` immediately on the
    ``NEEDS_REVIEW`` transition, and the next-slice router fills the
    routing/validation fields when it picks a path. A version with
    ``confidence_score = None`` is one the scorer was disabled for
    (``KW_HITL_DISABLE_SCORER=true``) at the time it transitioned.
    """

    version_id: str
    confidence_score: ConfidenceScore | None = None
    routing_decision: RoutingMethod | None = None
    validation_method: ValidationMethod | None = None
    validation_actor: str | None = None


# ---------------------------------------------------------------------------
# Auto-promotion worker result shapes (ADR-023 §6, EPIC-A A.4, slice 3, #215).
# ---------------------------------------------------------------------------
#
# The :class:`HITLAutoPromoter` (in
# :mod:`app.services.hitl_auto_promoter`) returns one
# :class:`AutoPromoteResult` per pass over the pending auto-routed
# versions. Each version lands in exactly one of three buckets:
#
# - ``promoted`` — successfully driven NEEDS_REVIEW → VALIDATED.
# - ``skipped`` — race-safe no-op (validation_method already set, version
#   no longer needs review, etc.). Reason is a closed enum so audit
#   queries can bucket without string cleanup.
# - ``failed`` — exception during promotion. The pass continues.
#
# These shapes back the :class:`POST /admin/hitl/run_auto_promote_pass`
# admin trigger response so operators see exactly what happened in the
# pass without grepping logs.


SkippedReason = Literal[
    "already_validated",
    "version_no_longer_needs_review",
    "document_not_found",
    "version_not_found",
]
"""Closed vocabulary of reasons the worker may skip a pending row.

- ``already_validated`` — the row's ``validation_method`` is already
  set (a previous worker pass or a human reviewer beat us). The
  ``list_pending_auto_promotions`` filter excludes these by default,
  but a row that flips between list-and-act stays race-safe by being
  reported here rather than crashing.
- ``version_no_longer_needs_review`` — the version's FSM status is no
  longer ``NEEDS_REVIEW``. A human reviewer raced ahead, or the version
  was rejected, or it transitioned to FAILED. Either way, the worker
  must not try to validate it.
- ``document_not_found`` / ``version_not_found`` — the parent document
  or the version itself disappeared between the list and the act
  (defensive; should not happen given the FK in migration 0007).
"""


class PromotedVersion(BaseModel):
    """One version the worker successfully promoted to VALIDATED.

    ``score_overall`` is the ``ConfidenceScore.overall`` the router
    used to pick ``auto`` — surfaced so the admin response carries the
    audit-relevant context without a follow-up read.
    """

    document_id: str
    version_id: str
    score_overall: float


class SkippedVersion(BaseModel):
    """One version the worker skipped (race-safe no-op).

    ``reason`` is one of :data:`SkippedReason`; see the docstring for
    the vocabulary.
    """

    document_id: str
    version_id: str
    reason: SkippedReason


class FailedVersion(BaseModel):
    """One version whose promotion raised an exception.

    The error string carries the exception's message; the worker logs
    the full traceback at ``error`` level so operators can correlate
    via the structured event ``hitl.auto_promote.version_failed``.
    """

    document_id: str
    version_id: str
    error: str


class AutoPromoteResult(BaseModel):
    """One pass over the pending auto-routed versions (slice 3, #215).

    Returned by :meth:`HITLAutoPromoter.run_pass` and surfaced by the
    admin trigger route so operators see the exact set of versions the
    pass touched, with the reason for every skip and the message for
    every failure. ``scanned`` counts every row pulled from the store
    in the pass — it equals ``len(promoted) + len(skipped) + len(failed)``
    by construction.
    """

    scanned: int
    promoted: list[PromotedVersion]
    skipped: list[SkippedVersion]
    failed: list[FailedVersion]


__all__ = [
    "AutoPromoteResult",
    "ConfidenceScore",
    "FailedVersion",
    "PromotedVersion",
    "RoutingDecision",
    "RoutingMethod",
    "RoutingReason",
    "SkippedReason",
    "SkippedVersion",
    "ValidationMetadata",
    "ValidationMethod",
]
