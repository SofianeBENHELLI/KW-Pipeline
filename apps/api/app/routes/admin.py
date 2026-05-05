"""Admin / health endpoints.

Holds:

* ``GET /health`` — minimal liveness probe (parity with the legacy route).
* ``GET /admin/config`` — sanitized configuration snapshot consumed by
  the Knowledge Forge Settings widget (``apps/_shared/settings-hub``).
  Strips every secret (API keys, auth tokens, DB passwords) before
  returning. Gated on the ``admin`` role (#83 slice 2 / ADR-019 §3).
* ``POST /admin/archive/unarchive`` — clears ``documents.archived_at``
  on a flag-archived row. ADR-027 §1.1, slice 1 of D.9.
* ``POST /admin/archive/relink_scope`` — reactivates a soft-removed
  ``document_scopes`` row via the existing ``add_scope`` reactivation
  path. ADR-027 §1.2, slice 2 of D.9.
* ``POST /admin/archive/purge_artifacts`` — physically deletes a
  document's source artifacts (bytes / extractions / semantic JSON /
  Markdown asset) via :meth:`StorageService.delete`, flips every
  version to ``PURGED``, overwrites ``storage_uri`` with a tombstone
  marker. ADR-027 §1.3, slice 4 of D.9. Catalog row preserved per the
  no-delete policy.
* ``POST /admin/archive/purge_batch`` — bulk wrapper around
  ``purge_artifacts``, capped at 100 ids per call, best-effort with
  per-doc error reporting. ADR-027 §4, slice 5 of D.9.

Every archive route requires the ``admin`` role (ADR-019 §3 / #264) AND
``?confirm=true`` for non-dry-run mutating actions (defence in depth,
per ADR-027 §5). ``?dry_run=true`` returns the impact summary with no
state change and no audit row. The 410 Gone read response for purged
versions / fully-purged documents is wired in
:mod:`app.routes.lifecycle` (slice 6 of D.9).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.dependencies import PipelineServices
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.admin_archive import (
    PurgeArtifactsRequest,
    PurgeArtifactsResponse,
    PurgeBatchRequest,
    PurgeBatchResponse,
    PurgeBatchResult,
    RelinkScopeRequest,
    RelinkScopeResponse,
    UnarchiveRequest,
    UnarchiveResponse,
    VersionPurgeResult,
)
from app.schemas.admin_config import (
    AdminConfigResponse,
    AuditConfig,
    CorsConfig,
    EmbeddingsConfig,
    HitlConfig,
    IteropConfig,
    KnowledgeLayerConfig,
    LLMConfig,
    LoggingConfig,
    NerConfig,
    PersistenceConfig,
    TaxonomyConfig,
    UploadConfig,
)
from app.schemas.document import HealthResponse
from app.schemas.scope import Scope
from app.services.auth import User, require_admin
from app.settings import Settings

log = logging.getLogger(__name__)


def _build_admin_config(settings: Settings) -> AdminConfigResponse:
    """Project a :class:`Settings` instance onto the public response shape.

    Secrets are reduced to a ``configured: bool``. Non-secret fields
    (model ids, paths, workflow refs, log level) are surfaced verbatim.
    """
    return AdminConfigResponse(
        upload=UploadConfig(
            max_bytes=settings.max_upload_bytes,
            allowed_content_types=sorted(settings.allowed_content_types),
        ),
        cors=CorsConfig(
            allowed_origins=settings.cors_allowed_origins,
            allowed_origin_regex=settings.cors_allowed_origin_regex,
        ),
        persistence=PersistenceConfig(
            persistent=settings.persistent,
            data_dir=settings.data_dir,
        ),
        knowledge_layer=KnowledgeLayerConfig(
            enabled=settings.knowledge_layer_enabled,
            neo4j_configured=bool(
                settings.neo4j_uri and settings.neo4j_user
                # neo4j_password may legitimately be empty in dev, so
                # we don't require it for ``configured`` semantics.
            ),
            neo4j_database=settings.neo4j_database,
        ),
        llm=LLMConfig(
            configured=bool(settings.anthropic_api_key),
            model=settings.anthropic_model,
            max_input_tokens_per_document=settings.entity_extractor_max_input_tokens_per_document,
        ),
        embeddings=EmbeddingsConfig(
            configured=bool(settings.voyage_api_key),
            model=settings.embedding_model,
        ),
        taxonomy=TaxonomyConfig(
            path=settings.taxonomy_path,
            cosine_threshold=settings.taxonomy_cosine_threshold,
        ),
        ner=NerConfig(
            enabled=settings.ner_enabled,
            spacy_model=settings.ner_spacy_model,
        ),
        audit=AuditConfig(
            enabled=settings.audit_enabled,
            db_path=settings.audit_db_path,
        ),
        hitl=HitlConfig(
            default_validation_method=settings.hitl_default_validation_method,
            iterop=IteropConfig(
                enabled=settings.iterop_enabled,
                workflow_ref=settings.iterop_workflow_ref,
                base_url_configured=bool(settings.iterop_base_url),
                auth_configured=bool(settings.iterop_auth_token),
            ),
        ),
        logging=LoggingConfig(
            format=settings.log_format,
            level=settings.log_level.upper(),
        ),
    )


_PURGE_BATCH_MAX = 100
"""Per-request cap on :func:`purge_batch` document ids (ADR-027 §4).

A list longer than this returns 422; chaining multiple calls is the
documented escape hatch. Kept as a module constant so tests can
import the same value rather than hard-coding 100 in the assertion.
"""


def _build_tombstone_uri(document_id: str, version_id: str, purged_at: datetime) -> str:
    """Return the ADR-027 §3 tombstone URI for a purged version.

    Shape: ``tombstone:purged:<document_id>:<version_id>:<purged_at_iso>``.
    The tombstone is parseable so audit tooling can recover context
    without joining against the audit log; it is also obviously not a
    real URI, so any storage backend that accidentally receives it
    fails the standard "not found" path rather than fetching unrelated
    bytes. Future read paths can ``startswith("tombstone:")`` to
    detect purged content.
    """
    return f"tombstone:purged:{document_id}:{version_id}:{purged_at.isoformat()}"


def _require_confirm_or_dry_run(*, confirm: bool, dry_run: bool) -> None:
    """Enforce ADR-027 §5: every non-dry-run mutating route needs ``?confirm=true``.

    Mirrors the ADR-027 §2 "exclusive" rule too — passing both
    ``?confirm=true`` and ``?dry_run=true`` rejects with 400 because a
    dry-run does not need confirmation (it does not mutate state). The
    "missing confirm" case maps to 422 with ``KW_UNPROCESSABLE_ENTITY``
    so a curl typo or a misconfigured admin UI surfaces a deterministic
    error instead of silently mutating state.
    """
    if dry_run and confirm:
        raise ApiError(
            status_code=400,
            code=ErrorCode.BAD_REQUEST,
            message="dry_run and confirm are mutually exclusive.",
            retryable=False,
            remediation=(
                "Pass either ?dry_run=true (impact summary, no "
                "mutation) or ?confirm=true (real mutation), not both."
            ),
        )
    if not dry_run and not confirm:
        raise ApiError(
            status_code=422,
            code=ErrorCode.UNPROCESSABLE_ENTITY,
            message="Missing required confirmation for mutating admin action.",
            retryable=False,
            remediation=(
                "Re-send with ?confirm=true to mutate state, or "
                "?dry_run=true to preview the impact without mutating."
            ),
        )


def build_admin_router(services: PipelineServices) -> APIRouter:
    """Register admin / health routes.

    ``services`` is captured by closure so the archive routes can
    reach the catalog store and reactivate / unarchive rows. Per
    other ``build_*_router`` factories the legacy ``/health`` and
    ``/admin/config`` routes are unchanged.
    """
    router = APIRouter()

    @router.get("/health", operation_id="health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get(
        "/admin/config",
        operation_id="admin_config",
        response_model=AdminConfigResponse,
    )
    def admin_config(
        _user: User = Depends(require_admin),
    ) -> AdminConfigResponse:
        # Re-read settings on every request so ``monkeypatch.setenv``
        # in tests is observed without restarting the app — same
        # posture every other call site uses.
        return _build_admin_config(Settings())

    @router.post(
        "/admin/archive/unarchive",
        operation_id="admin_archive_unarchive",
        response_model=UnarchiveResponse,
    )
    def unarchive_document(
        body: UnarchiveRequest,
        confirm: bool = Query(False),
        dry_run: bool = Query(False),
        user: User = Depends(require_admin),
    ) -> UnarchiveResponse:
        """Clear ``documents.archived_at`` on a flag-archived row (ADR-027 §1.1).

        The catalog method is idempotent on already-active documents,
        so this route's idempotency story is also "200 OK + no audit
        row" rather than a 409 — the ADR-027 §1.1 example shows 409
        for the bytes-purge precondition, not for unarchive. Real
        operator workflows re-run the route safely as a status check.
        """
        _require_confirm_or_dry_run(confirm=confirm, dry_run=dry_run)

        # Use the archived-inclusive accessor so a flag-archived row
        # still resolves — the standard ``get_document`` would 404 it,
        # which is exactly the behaviour we're trying to reverse.
        catalog = services.documents.catalog
        document = catalog._get_document_including_archived(  # type: ignore[attr-defined]
            body.document_id,
        )
        if document is None:
            raise ApiError(
                status_code=404,
                code=ErrorCode.NOT_FOUND,
                message=f"Document not found: {body.document_id!r}.",
                retryable=False,
                remediation=(
                    "Verify the document id; archived rows are "
                    "visible to this admin tool but a never-existed "
                    "id still 404s."
                ),
            )

        archived_at_before = document.archived_at

        if dry_run:
            # No state change, no audit row — just the impact summary.
            return UnarchiveResponse(
                document_id=body.document_id,
                archived_at_before=archived_at_before,
                unarchived_at=None,
                dry_run=True,
            )

        # Real mutation. ``unarchive_document`` is idempotent: when
        # the row was already active the UPDATE matches zero rows and
        # the returned :class:`Document` carries ``archived_at = None``.
        catalog.unarchive_document(body.document_id, actor=user.id)
        unarchived_at = datetime.now(UTC) if archived_at_before is not None else None

        # Audit emit only fires on a real transition — an idempotent
        # no-op (already-active doc) leaves the log clean. The dotted
        # event name routes through the audit handler installed at
        # startup; payload mirrors the ADR-027 §1.1 contract
        # (``document_id``, ``archived_at_before``, ``actor``).
        if unarchived_at is not None:
            log.info(
                "admin.document.unarchived",
                extra={
                    "document_id": body.document_id,
                    "archived_at_before": archived_at_before.isoformat()
                    if archived_at_before
                    else None,
                    "unarchived_at": unarchived_at.isoformat(),
                    "actor": user.id,
                    "actor_role": user.role,
                },
            )

        return UnarchiveResponse(
            document_id=body.document_id,
            archived_at_before=archived_at_before,
            unarchived_at=unarchived_at,
            dry_run=False,
        )

    @router.post(
        "/admin/archive/relink_scope",
        operation_id="admin_archive_relink_scope",
        response_model=RelinkScopeResponse,
    )
    def relink_scope(
        body: RelinkScopeRequest,
        confirm: bool = Query(False),
        dry_run: bool = Query(False),
        user: User = Depends(require_admin),
    ) -> RelinkScopeResponse:
        """Reactivate a soft-removed ``document_scopes`` row (ADR-027 §1.2).

        Wraps the existing :meth:`CatalogStore.add_scope` reactivation
        path from #262: that method already clears ``removed_at`` and
        overwrites ``added_at`` / ``added_by`` with the new caller's
        identity when the row was previously soft-removed. The admin
        wrapper just gates the action behind ``require_admin`` plus
        ``?confirm=true`` and surfaces the pre-action ``removed_at``
        timestamp for the audit log.
        """
        _require_confirm_or_dry_run(confirm=confirm, dry_run=dry_run)

        catalog = services.documents.catalog
        link_before = catalog.get_scope_link(
            body.document_id,
            body.scope_kind,
            body.scope_ref,
        )
        if link_before is None:
            raise ApiError(
                status_code=404,
                code=ErrorCode.NOT_FOUND,
                message=(
                    f"Scope link not found: ({body.document_id!r}, "
                    f"{body.scope_kind!r}, {body.scope_ref!r})."
                ),
                retryable=False,
                remediation=(
                    "Verify the document id and (kind, ref) tuple; "
                    "the admin tool can reach soft-removed links but "
                    "a triple that never existed still 404s."
                ),
            )

        removed_at_before = link_before.removed_at

        if dry_run:
            return RelinkScopeResponse(
                document_id=body.document_id,
                scope_kind=body.scope_kind,
                scope_ref=body.scope_ref,
                removed_at_before=removed_at_before,
                relinked_at=None,
                dry_run=True,
            )

        # Drive the existing add_scope reactivation path — it clears
        # removed_at and overwrites added_at / added_by with the new
        # admin actor's identity, which is the documented "re-link is
        # a fresh audit event" behaviour from #262.
        relinked_at = datetime.now(UTC)
        catalog.add_scope(
            body.document_id,
            Scope(
                kind=body.scope_kind,
                ref=body.scope_ref,
                added_at=relinked_at,
                added_by=user.id,
            ),
        )

        # Audit emit only fires on a real transition — re-linking an
        # already-active row is a no-op for the catalog (first-write
        # wins for active rows per #262) and we mirror that on the
        # audit log so the table doesn't fill with no-op rows.
        if removed_at_before is not None:
            log.info(
                "admin.scope_link.relinked",
                extra={
                    "document_id": body.document_id,
                    "scope_kind": body.scope_kind,
                    "scope_ref": body.scope_ref,
                    "removed_at_before": removed_at_before.isoformat(),
                    "relinked_at": relinked_at.isoformat(),
                    "actor": user.id,
                    "actor_role": user.role,
                },
            )

        # ``relinked_at`` is the moment we reactivated; for an
        # already-active row we still return the moment the route
        # ran so clients can correlate without a separate "was it a
        # no-op?" probe — the empty audit log is the canonical
        # idempotency signal.
        return RelinkScopeResponse(
            document_id=body.document_id,
            scope_kind=body.scope_kind,
            scope_ref=body.scope_ref,
            removed_at_before=removed_at_before,
            relinked_at=relinked_at if removed_at_before is not None else None,
            dry_run=False,
        )

    def _purge_one_document(
        document_id: str,
        *,
        dry_run: bool,
        actor: str,
        actor_role: str,
    ) -> PurgeArtifactsResponse:
        """Apply ADR-027 §1.3 ``purge_artifacts`` to a single document.

        Shared between the per-doc route and the bulk wrapper. Raises
        :class:`ApiError` for the documented HTTP-mappable failures
        (404 missing, 409 not-archived); the bulk wrapper catches
        those and converts to a per-row error envelope so a single
        failure doesn't abort the batch.
        """
        catalog = services.documents.catalog
        document = catalog._get_document_including_archived(  # type: ignore[attr-defined]
            document_id,
        )
        if document is None:
            raise ApiError(
                status_code=404,
                code=ErrorCode.NOT_FOUND,
                message=f"Document not found: {document_id!r}.",
                retryable=False,
                remediation=(
                    "Verify the document id; archived rows are "
                    "visible to this admin tool but a never-existed "
                    "id still 404s."
                ),
            )
        if document.archived_at is None:
            # ADR-027 §1.3 precondition: archive-then-purge is the
            # ordered ritual. Fail closed so an admin must run the
            # archive step (or wait for the orphan cascade) first;
            # the unarchive escape hatch in §1.1 is the only way
            # back from an over-eager archive.
            raise ApiError(
                status_code=409,
                code=ErrorCode.CONFLICT,
                message=(
                    f"Document {document_id!r} is not archived; "
                    "purge_artifacts requires the document to be "
                    "archived first."
                ),
                retryable=False,
                remediation=(
                    "Archive the document via the orphan cascade or a "
                    "future archive admin route, then re-run "
                    "purge_artifacts. The /admin/archive/unarchive "
                    "route reverses the archive flag if you change "
                    "your mind before purging."
                ),
            )

        purged_at = datetime.now(UTC)
        results: list[VersionPurgeResult] = []
        for version in list(document.versions):
            tombstone_uri = _build_tombstone_uri(document_id, version.id, purged_at)
            status_before = version.status
            storage_uri_before = version.storage_uri

            if status_before is DocumentVersionStatus.PURGED:
                # Idempotent re-purge: the existing tombstone URI is
                # already on the row. Echo it back without touching
                # storage / catalog / audit so a retry converges
                # cleanly (ADR-027 §1.3 + §6).
                results.append(
                    VersionPurgeResult(
                        version_id=version.id,
                        status_before=status_before,
                        storage_uri_before=storage_uri_before,
                        tombstone_uri=storage_uri_before,
                        purged_at=None,
                        bytes_estimate=version.file_size,
                    )
                )
                continue

            if dry_run:
                # Impact summary only — no storage delete, no catalog
                # mutation, no audit row.
                results.append(
                    VersionPurgeResult(
                        version_id=version.id,
                        status_before=status_before,
                        storage_uri_before=storage_uri_before,
                        tombstone_uri=tombstone_uri,
                        purged_at=None,
                        bytes_estimate=version.file_size,
                    )
                )
                continue

            # Real mutation. Storage delete first (best-effort,
            # idempotent per the slice 3 contract) so a partial
            # failure leaves bytes deleted before the catalog is
            # touched — that matches the §6 "bytes are the point of
            # no return" envelope. A storage error is logged and we
            # carry on; the catalog flip + audit row capture the
            # before/after state.
            try:
                services.storage.delete(storage_uri_before)
            except Exception as exc:  # noqa: BLE001 — best-effort delete; logged + continue.
                log.warning(
                    "admin.purge_artifacts.storage_delete_failed",
                    extra={
                        "document_id": document_id,
                        "version_id": version.id,
                        "storage_uri": storage_uri_before,
                        "error": str(exc),
                    },
                )
            services.documents.catalog.purge_version_artifacts(
                document_id,
                version.id,
                tombstone_uri=tombstone_uri,
                purged_at=purged_at,
                actor=actor,
            )
            log.info(
                "document.artifacts_purged",
                extra={
                    "document_id": document_id,
                    "version_id": version.id,
                    "storage_uri_before": storage_uri_before,
                    "tombstone_uri": tombstone_uri,
                    "actor": actor,
                    "actor_role": actor_role,
                    "dry_run": False,
                },
            )
            results.append(
                VersionPurgeResult(
                    version_id=version.id,
                    status_before=status_before,
                    storage_uri_before=storage_uri_before,
                    tombstone_uri=tombstone_uri,
                    purged_at=purged_at,
                    bytes_estimate=version.file_size,
                )
            )

        return PurgeArtifactsResponse(
            document_id=document_id,
            versions_purged=results,
            dry_run=dry_run,
        )

    @router.post(
        "/admin/archive/purge_artifacts",
        operation_id="admin_archive_purge_artifacts",
        response_model=PurgeArtifactsResponse,
    )
    def purge_artifacts(
        body: PurgeArtifactsRequest,
        confirm: bool = Query(False),
        dry_run: bool = Query(False),
        user: User = Depends(require_admin),
    ) -> PurgeArtifactsResponse:
        """Hard-delete a document's source artifacts (ADR-027 §1.3).

        Pre-conditions:

        - The document must exist (404 otherwise).
        - The document must already be archived (409 otherwise) —
          archive-then-purge is the ordered ritual that gives an
          operator a chance to reverse via ``unarchive`` before bytes
          go.
        - ``?confirm=true`` is required for non-dry-run mutating
          actions (422 otherwise — ``KW_UNPROCESSABLE_ENTITY``).

        Per version the route:

        1. Computes a tombstone URI per ADR-027 §3
           (``tombstone:purged:<doc>:<version>:<iso>``).
        2. Calls :meth:`StorageService.delete` on the version's
           current ``storage_uri`` (best-effort + idempotent per
           ADR-027 §7).
        3. Flips the version's status to :data:`DocumentVersionStatus.PURGED`
           and overwrites ``storage_uri`` with the tombstone via
           :meth:`CatalogStore.purge_version_artifacts`.
        4. Emits a ``document.artifacts_purged`` audit event with
           the storage URI before / after, the actor, and the
           ``dry_run`` flag.

        Idempotent: re-purging an already-PURGED version is a no-op
        — the existing tombstone URI is echoed back and no audit
        row is emitted (the empty audit row is the idempotency
        signal). KG cleanup is out of scope: the cascade in #265
        already removed KG nodes when the document was archived.
        """
        _require_confirm_or_dry_run(confirm=confirm, dry_run=dry_run)
        return _purge_one_document(
            body.document_id,
            dry_run=dry_run,
            actor=user.id,
            actor_role=user.role,
        )

    @router.post(
        "/admin/archive/purge_batch",
        operation_id="admin_archive_purge_batch",
        response_model=PurgeBatchResponse,
    )
    def purge_batch(
        body: PurgeBatchRequest,
        confirm: bool = Query(False),
        dry_run: bool = Query(False),
        user: User = Depends(require_admin),
    ) -> PurgeBatchResponse:
        """Bulk wrapper around ``purge_artifacts`` (ADR-027 §4).

        Best-effort: a failure on one doc (e.g. ``document_not_archived``,
        ``document_not_found``) does not abort the batch — the
        per-doc failure surfaces as ``success=False`` +
        ``error_code`` / ``error_message`` in the corresponding
        :class:`PurgeBatchResult`. Successful per-doc purges carry
        the full :class:`PurgeArtifactsResponse` under
        ``purge_response`` so callers can recover the per-version
        tombstone URIs without a follow-up call.

        Capped at 100 ids per call (``KW_UNPROCESSABLE_ENTITY``);
        chaining is the documented escape hatch for larger sweeps.
        Each successful per-doc purge emits its own
        ``document.artifacts_purged`` audit row — never a single
        batch-level row, so the audit log stays queryable per
        document.
        """
        _require_confirm_or_dry_run(confirm=confirm, dry_run=dry_run)
        if len(body.document_ids) > _PURGE_BATCH_MAX:
            raise ApiError(
                status_code=422,
                code=ErrorCode.UNPROCESSABLE_ENTITY,
                message=(
                    "purge_batch accepts at most "
                    f"{_PURGE_BATCH_MAX} document_ids per call; "
                    f"got {len(body.document_ids)}."
                ),
                retryable=False,
                remediation=(
                    "Split the request into batches of "
                    f"{_PURGE_BATCH_MAX} or fewer ids and chain the "
                    "calls. Each batch is independent — a failure in "
                    "one batch does not affect the others."
                ),
            )

        results: list[PurgeBatchResult] = []
        for document_id in body.document_ids:
            try:
                response = _purge_one_document(
                    document_id,
                    dry_run=dry_run,
                    actor=user.id,
                    actor_role=user.role,
                )
            except ApiError as exc:
                # Per-doc failure: surface the public error code so
                # the caller can route on it (e.g. retry the
                # ``document_not_archived`` rows after archiving them
                # via the cascade). The batch itself still returns
                # 200 — partial success is the contract.
                results.append(
                    PurgeBatchResult(
                        document_id=document_id,
                        success=False,
                        error_code=exc.code,
                        error_message=exc.message,
                        purge_response=None,
                    )
                )
                continue
            results.append(
                PurgeBatchResult(
                    document_id=document_id,
                    success=True,
                    error_code=None,
                    error_message=None,
                    purge_response=response,
                )
            )

        return PurgeBatchResponse(results=results, dry_run=dry_run)

    return router
