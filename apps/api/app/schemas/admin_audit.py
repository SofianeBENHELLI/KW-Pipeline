"""Request/response shapes for the Admin Audit Log Viewer (#206 follow-up).

The viewer is a read-only operator surface over the structured audit
event vocabulary (the same ``audit_events`` table the existing
admin-archive routes already write to). One paginated GET endpoint
backs the UI; the response carries the per-row payload verbatim so
the operator can inspect the full structured-logging payload without
a second probe.

``AuditEventItem.id`` is a synthesised stable identifier — the
audit table's primary key is internal (in-memory store has no
monotonic id at all), so the wire shape uses a derived
``f"{ts_utc}:{event_name}:{actor or '-'}"`` triple. The UI uses it
as a React key only; the cursor pagination is independent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel


class AuditEventItem(BaseModel):
    """One row of the audit log viewer table.

    ``payload`` is the full ``extra`` dict the structured-logging
    emitter passed to ``log.info(...)`` when the event fired —
    surfaced verbatim so the operator can inspect the per-event
    context (document_id, before/after fields, scope kind/ref, etc.)
    without joining against any other surface.

    ``actor`` is projected out of the payload (audit emitters stash
    it under ``payload['actor']``); ``None`` when the event was
    emitted by a system-initiated cascade with no human principal
    (e.g. the orphan archive cascade).
    """

    id: str = Field(
        description=(
            "Stable display id synthesised from the row's wall-clock + "
            "event name + actor. Opaque — the UI uses it as a React "
            "key only; do not parse it."
        ),
    )
    event_name: str = Field(
        description=(
            "Dotted event name, e.g. ``routing.decided``, "
            "``review.validated``, ``document.archived_orphan``. The "
            "full vocabulary is documented in "
            "``docs/architecture/observability.md``."
        ),
    )
    actor: str | None = Field(
        default=None,
        description=(
            "Acting principal — the user id the route stamped onto "
            "``payload['actor']`` (or ``None`` for system-initiated "
            "events with no human caller)."
        ),
    )
    created_at: datetime = Field(
        description="Wall clock the event was emitted (UTC, seconds resolution).",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Full structured-logging payload. Arbitrary JSON-shaped — "
            "the UI renders it as pretty JSON in the row's expanded "
            "panel."
        ),
    )


class AdminAuditEventsResponse(BaseModel):
    """Response body of ``GET /admin/audit/events`` (#206 follow-up).

    Cursor-paginated, sorted ``created_at DESC`` so the freshest
    events surface at the top of the table. ``next_cursor`` is opaque
    (the audit store's base64-JSON codec) and ``None`` when this page
    is the last one.

    ``available_event_names`` is a one-shot ``SELECT DISTINCT
    event_name`` against the store — included on every response so
    the UI's filter dropdown doesn't need a second probe to populate.
    Cheap by construction (the audit table is small and the store
    indexes on ``event_name``).
    """

    items: list[AuditEventItem] = Field(default_factory=list)
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque cursor for the next page. ``None`` means this "
            "page is the last one. Pass it back as ``?cursor=...`` "
            "on the next request."
        ),
    )
    available_event_names: list[str] = Field(
        default_factory=list,
        description=(
            "Distinct ``event_name`` values currently in the store, "
            "sorted lexicographically. The UI's filter dropdown is "
            "populated from this list so a deployment that has never "
            "fired a particular event doesn't surface it as a filter "
            "option."
        ),
    )


__all__ = [
    "AdminAuditEventsResponse",
    "AuditEventItem",
]
