"""Request/response shapes for the Archive/Purge Admin tool (ADR-027).

Slices 1+2 of the admin tool — ``unarchive`` and ``relink_scope`` —
ship the basics: request bodies for the two admin actions and
response envelopes that mirror the ADR-027 §1.1 / §1.2 examples.

Slices 4+5+6 add the bytes-deletion surface: ``purge_artifacts``
(per-document), ``purge_batch`` (bulk, max 100), the
:class:`VersionPurgeResult` per-version row that carries the
tombstone URI, and the per-batch :class:`PurgeBatchResult` envelope
that lets a single doc fail without aborting the whole list.

Slice D.9 (Admin UI) adds the read-side listing surface:
:class:`ArchivedDocumentItem` + :class:`ArchivedDocumentsResponse`
power ``GET /admin/archive/archived_documents`` so the operator UI
can paginate over flag-archived rows without a per-doc probe.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.document import DocumentVersionStatus
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


class PurgeArtifactsRequest(BaseModel):
    """Body for ``POST /admin/archive/purge_artifacts`` (ADR-027 §1.3)."""

    document_id: str = Field(
        description=(
            "Catalog id of the document whose source artifacts (bytes, "
            "extractions, semantic JSON, Markdown asset) should be "
            "physically deleted. Resolved via the archived-inclusive "
            "accessor; the route 409s if the document isn't archived "
            "first per the §1.3 archive-then-purge precondition."
        ),
    )


class VersionPurgeResult(BaseModel):
    """Per-version purge outcome inside a :class:`PurgeArtifactsResponse`.

    ``status_before`` is the version's status at the moment the route
    sampled it — useful when the purge fans out across a multi-version
    family with mixed terminal states (VALIDATED, REJECTED, FAILED,
    SUPERSEDED). ``storage_uri_before`` is the original URI the
    storage backend was holding the bytes under, surfaced for the
    audit row payload (also included on the response so a dry-run
    caller can preview which URIs would be deleted).

    ``tombstone_uri`` is the post-purge ``storage_uri`` per ADR-027
    §3 (``tombstone:purged:<doc>:<version>:<iso>``); future read
    paths can ``startswith("tombstone:")`` to detect purged content
    without joining against the audit log.

    ``purged_at`` is ``None`` for a dry-run (no state change, the
    timestamp doesn't exist yet) and the wall clock the route
    stamped onto the audit event for a real mutation. ``bytes_estimate``
    is the version's ``file_size`` if the catalog still holds it —
    purely informational, surfaced so an operator can sanity-check
    the freed-bytes total.
    """

    version_id: str
    status_before: DocumentVersionStatus
    storage_uri_before: str
    tombstone_uri: str
    purged_at: datetime | None = None
    bytes_estimate: int | None = None


class PurgeArtifactsResponse(BaseModel):
    """Response body for ``POST /admin/archive/purge_artifacts`` (ADR-027 §1.3).

    ``versions_purged`` carries one row per version in the document
    family — including versions that were already ``PURGED`` (the
    idempotent re-purge case), in which case the row's
    ``status_before`` is ``PURGED`` and the existing tombstone URI is
    echoed back so callers can correlate without re-reading.

    ``dry_run`` mirrors the request's ``?dry_run=true`` query param
    so clients can disambiguate the impact preview from a real
    mutation without re-reading the URL — same pattern as the other
    admin-archive responses.
    """

    document_id: str
    versions_purged: list[VersionPurgeResult] = Field(default_factory=list)
    dry_run: bool = False


class PurgeBatchRequest(BaseModel):
    """Body for ``POST /admin/archive/purge_batch`` (ADR-027 §4).

    Capped at 100 ids per call; the route returns 422 with
    ``KW_UNPROCESSABLE_ENTITY`` for longer lists. Chaining multiple
    calls is the documented escape hatch for larger sweeps.
    """

    document_ids: list[str] = Field(
        description=(
            "Catalog ids to purge, max 100 per call. Each doc is "
            "processed independently — a failure on one (e.g. "
            "``document_not_archived``) does not abort the batch."
        ),
    )


class PurgeBatchResult(BaseModel):
    """Per-document outcome inside a :class:`PurgeBatchResponse`.

    ``success`` discriminates the union: when ``True``, ``purge_response``
    carries the full per-document :class:`PurgeArtifactsResponse`;
    when ``False``, ``error_code`` + ``error_message`` describe what
    went wrong. Mirrors the :class:`BatchUploadOutcome` shape so the
    frontend's batch-result renderer is uniform across admin and
    upload surfaces.
    """

    document_id: str
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    purge_response: PurgeArtifactsResponse | None = None


class PurgeBatchResponse(BaseModel):
    """Response body for ``POST /admin/archive/purge_batch`` (ADR-027 §4).

    Always HTTP 200 — per-doc failures are reported in
    ``results[i]``. ``dry_run`` mirrors the ``?dry_run=true`` query
    param for the same reason as the per-doc response.
    """

    results: list[PurgeBatchResult] = Field(default_factory=list)
    dry_run: bool = False


class ArchivedDocumentItem(BaseModel):
    """One row of :class:`ArchivedDocumentsResponse` — a flag-archived document.

    Surface fields the admin UI needs to render an Archive view row
    without a per-doc detail fetch:

    - ``document_id`` / ``original_filename`` identify the row.
    - ``archived_at`` is the wall clock the orphan cascade (or a
      future explicit-archive route) stamped on the document; the UI
      renders it as a relative date.
    - ``last_active_scope_kind`` / ``last_active_scope_ref`` describe
      the scope that was last removed before the cascade flagged the
      document — so the admin can see *why* it ended up archived. Both
      fall back to ``None`` when no scope-link history is recoverable
      (e.g. a never-scoped document was archived directly).
    - ``versions_purged`` / ``versions_remaining`` split the version
      family by :data:`DocumentVersionStatus.PURGED` so the operator
      sees how much bytes-recovery surface is still on disk vs already
      tombstone'd. The standard ``X / Y`` UI string is
      ``versions_remaining / (versions_remaining + versions_purged)``.
    """

    document_id: str
    original_filename: str
    archived_at: datetime
    last_active_scope_kind: str | None = None
    last_active_scope_ref: str | None = None
    versions_purged: int = 0
    versions_remaining: int = 0


class ArchivedDocumentsResponse(BaseModel):
    """Response body for ``GET /admin/archive/archived_documents``.

    Cursor-paginated walk of every flag-archived row in the catalog,
    sorted by ``archived_at DESC`` (most-recently archived first) so
    the admin UI's default view surfaces the freshest archives at the
    top of the table.

    ``next_cursor`` is opaque (the catalog's existing base64-JSON cursor
    codec) and ``None`` when this page is the last one — same shape as
    :class:`DocumentListResponse`.
    """

    items: list[ArchivedDocumentItem] = Field(default_factory=list)
    next_cursor: str | None = None


__all__ = [
    "ArchivedDocumentItem",
    "ArchivedDocumentsResponse",
    "PurgeArtifactsRequest",
    "PurgeArtifactsResponse",
    "PurgeBatchRequest",
    "PurgeBatchResponse",
    "PurgeBatchResult",
    "RelinkScopeRequest",
    "RelinkScopeResponse",
    "UnarchiveRequest",
    "UnarchiveResponse",
    "VersionPurgeResult",
]
