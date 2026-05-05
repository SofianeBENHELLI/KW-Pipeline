"""Request/response shapes for the Archive/Purge Admin tool (ADR-027).

Slices 1+2 of the admin tool — ``unarchive`` and ``relink_scope`` —
ship the basics: request bodies for the two admin actions and
response envelopes that mirror the ADR-027 §1.1 / §1.2 examples.

The harder slices (``purge_artifacts``, ``purge_batch``, the 410 Gone
read response, the ``PURGED`` status migration) are deferred to a
separate PR and intentionally not modelled here yet — adding them
piecemeal keeps the slice review small and avoids a half-finished
``PURGED`` enum leaking onto the OpenAPI snapshot.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel
from app.schemas.scope import ScopeKind


class UnarchiveRequest(BaseModel):
    """Body for ``POST /admin/archive/unarchive`` (ADR-027 §1.1)."""

    document_id: str = Field(
        description=(
            "Catalog id of the document whose ``archived_at`` flag "
            "should be cleared. Looked up via the archived-inclusive "
            "accessor so a flag-archived row still resolves."
        ),
    )


class UnarchiveResponse(BaseModel):
    """Response body for ``POST /admin/archive/unarchive`` (ADR-027 §1.1).

    ``archived_at_before`` is the ``archived_at`` timestamp the row
    carried before the call; ``None`` when the document was already
    active (the idempotent no-op case). ``unarchived_at`` is the wall
    clock the route stamped onto the audit event; ``None`` for a
    dry-run (no audit row was written) or for the idempotent no-op
    (no transition fired).

    ``dry_run`` mirrors the request's ``?dry_run=true`` query param so
    clients can disambiguate the "would-have-mutated" payload from a
    real mutation without re-reading the URL.
    """

    document_id: str
    archived_at_before: datetime | None = None
    unarchived_at: datetime | None = None
    dry_run: bool = False


class RelinkScopeRequest(BaseModel):
    """Body for ``POST /admin/archive/relink_scope`` (ADR-027 §1.2)."""

    document_id: str = Field(
        description="Catalog id of the document whose scope link to re-activate.",
    )
    scope_kind: ScopeKind = Field(
        description=(
            "Scope flavor — ``personal``, ``swym_community``, or "
            "``project``. Matches the ``ScopeKind`` literal from "
            "ADR-020 §1."
        ),
    )
    scope_ref: str = Field(
        description=(
            "Scope reference (community id, user id, project id) — "
            "interpretation depends on ``scope_kind``."
        ),
    )


class RelinkScopeResponse(BaseModel):
    """Response body for ``POST /admin/archive/relink_scope`` (ADR-027 §1.2).

    ``removed_at_before`` is the soft-removal timestamp the link
    carried before the call; ``None`` when the link was already
    active (the idempotent no-op case). ``relinked_at`` is the wall
    clock the route stamped onto the audit event; ``None`` for a
    dry-run or the idempotent no-op.
    """

    document_id: str
    scope_kind: ScopeKind
    scope_ref: str
    removed_at_before: datetime | None = None
    relinked_at: datetime | None = None
    dry_run: bool = False


__all__ = [
    "RelinkScopeRequest",
    "RelinkScopeResponse",
    "UnarchiveRequest",
    "UnarchiveResponse",
]
