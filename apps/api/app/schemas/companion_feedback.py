"""Wire shapes for the AURA companion feedback bridge (#371 / ADR-029).

The companion's per-citation feedback is the user-facing edge of the
**continuous learning loop** in the architecture diagram (Step 6 →
HITL re-review → Knowledge Update → Better AI Outcomes). This module
locks the request / record shapes ahead of the route so:

* the future ``POST /companion/feedback`` route is a thin wire
  adapter,
* the persistence layer (audit store / dedicated table — TBD when the
  route lands) writes a stable record shape from day 1,
* alternate companions (recommend / decide / act, EPIC #373) reuse
  the same envelope without re-inventing the reaction enum or the
  addressable handle pair (``answer_id``, ``citation_index``).

The route + persistence wiring land in a follow-up under EPIC #373;
this module is contract-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel

CompanionFeedbackReaction = Literal["helpful", "wrong", "incomplete"]
"""Closed reaction set. Adding a new reaction is an additive,
non-breaking change (per the ADR-029 back-compat policy on companion
contracts); removing one is breaking."""


class CompanionFeedbackRequest(BaseModel):
    """Wire shape of ``POST /companion/feedback`` (route TBD).

    Addresses a specific past response by its ``answer_id`` (locked
    in the :class:`~app.schemas.companion.GroundedAnswer` envelope)
    plus a ``citation_index`` into that response's ``citations[]``.
    Splitting the address this way keeps the request small — the
    consumer doesn't have to send the full citation back, just point
    at it — while letting the server re-hydrate the chunk on receipt.

    ``note`` is an optional free-text comment from the user; bounded
    on the schema side so a runaway client can't post novellas.
    """

    answer_id: str
    citation_index: int = Field(ge=0)
    reaction: CompanionFeedbackReaction
    note: str | None = Field(default=None, max_length=2000)


class CompanionFeedbackRecord(BaseModel):
    """Persisted shape — what the future feedback store writes.

    Resolved fields (``chunk_id``, ``document_id``, ``version_id``)
    are denormalised from the addressed citation at write time so
    aggregation queries don't need a join back through the answer
    table. ``recorded_at`` is server-side and authoritative; the
    request body never carries a timestamp.

    ``user_subject`` is the auth subject (or ``None`` for unauth /
    dev mode) — kept so the re-review trigger can debounce one user
    spamming "wrong" reactions on the same chunk.
    """

    answer_id: str
    citation_index: int = Field(ge=0)
    chunk_id: str
    document_id: str
    version_id: str
    reaction: CompanionFeedbackReaction
    note: str | None = None
    user_subject: str | None = None
    recorded_at: datetime


__all__ = [
    "CompanionFeedbackReaction",
    "CompanionFeedbackRecord",
    "CompanionFeedbackRequest",
]
