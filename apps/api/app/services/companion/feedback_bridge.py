"""Per-citation feedback aggregation for the AURA companion (#371 / ADR-029).

The continuous-learning loop in the architecture diagram closes when
a "wrong" reaction on a cited chunk eventually re-promotes that
chunk's parent document version into the Orbital re-review queue.
This module ships the **policy** half of that loop (threshold
predicate + audit event names) ahead of the route + persistence
plumbing, on the same lock-in posture as the trust gate (#372) and
citation contract (#370):

* The threshold predicate is pure — no DB, no clock, no audit
  emitter — so the future route is a thin wire/persistence adapter.
* The audit event names are constants here so any consumer that
  scrapes the audit store (operator dashboards, drift trackers)
  encodes against a stable identifier from day 1.

The actual ``POST /companion/feedback`` route, the persistence
layer, and the side effect of moving a version to ``NEEDS_REVIEW``
all land in a follow-up under EPIC #373; this module is contract +
pure-function only.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.schemas.companion_feedback import CompanionFeedbackRecord

# ─── Audit event names (stable public identifiers) ───────────────
# Convention matches existing names in the audit store
# (``orbital.document.purge``, ``orbital.knowledge_space.purge``):
# ``surface.entity.action``. Operators may grep for these in
# ``GET /admin/audit/events``; renames are breaking.

AUDIT_EVENT_FEEDBACK_RECORDED = "companion.feedback.recorded"
"""Emitted on every accepted ``POST /companion/feedback`` call.
Payload: the full :class:`CompanionFeedbackRecord` (sans ``note``
when redaction policy demands)."""

AUDIT_EVENT_FEEDBACK_TRIGGERED_REVIEW = "companion.feedback.triggered_review"
"""Emitted once when a chunk crosses the wrong-threshold and the
re-review side effect fires. Payload: ``{chunk_id, document_id,
version_id, wrong_count_in_window, window_days}``."""


def should_trigger_re_review(
    records: list[CompanionFeedbackRecord],
    *,
    chunk_id: str,
    threshold: int,
    window_days: int,
    now: datetime,
) -> bool:
    """Decide whether a chunk's accumulated feedback should trip a re-review.

    Pure function — caller passes the candidate records (typically
    the per-chunk slice of the feedback store), the policy knobs
    (from :class:`Settings`), and an explicit ``now`` so tests can
    pin time deterministically.

    Trigger rule:

    * Only ``"wrong"`` reactions count. ``"helpful"`` and
      ``"incomplete"`` are observed but don't promote a chunk back to
      review — incomplete is a knowledge-gap signal, not a fact-error
      signal.
    * Records outside ``[now - window_days, now]`` are ignored.
    * Each ``user_subject`` contributes at most once per window —
      this debounces a single user spam-clicking "wrong" and lifts
      the threshold's intent ("N independent users disagreed") above
      the literal "N reactions" reading.
    * ``records`` not matching ``chunk_id`` are ignored so callers
      can pass a broader slice without pre-filtering.
    """
    if threshold < 1 or window_days < 1:
        return False
    cutoff = now - timedelta(days=window_days)
    distinct_subjects: set[str | None] = set()
    anonymous_count = 0
    for record in records:
        if record.chunk_id != chunk_id:
            continue
        if record.reaction != "wrong":
            continue
        if record.recorded_at < cutoff or record.recorded_at > now:
            continue
        if record.user_subject is None:
            # Anonymous reactions can't be deduped per-subject; count
            # each one. Operators running with auth disabled accept
            # the noisier signal.
            anonymous_count += 1
        else:
            distinct_subjects.add(record.user_subject)
    return (len(distinct_subjects) + anonymous_count) >= threshold


__all__ = [
    "AUDIT_EVENT_FEEDBACK_RECORDED",
    "AUDIT_EVENT_FEEDBACK_TRIGGERED_REVIEW",
    "should_trigger_re_review",
]
