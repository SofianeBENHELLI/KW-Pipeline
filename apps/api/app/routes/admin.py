"""Admin / health endpoints.

Holds:

* ``GET /health`` — minimal liveness probe (parity with the legacy route).
* ``GET /ready`` — readiness probe. Returns 200 when the catalog
  answers and 503 when it does not. Optional dependencies (Neo4j when
  the knowledge layer is enabled) surface in ``checks`` but never gate
  readiness — see :class:`ReadyResponse` in ``app.schemas.document``.
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
* ``GET /admin/archive/archived_documents`` — paginated read of
  flag-archived documents (``archived_at IS NOT NULL``), sorted
  ``archived_at DESC``. Powers the Admin UI Archive view; D.9.
* ``POST /admin/hitl/run_auto_promote_pass`` — runs one synchronous
  pass of the HITL auto-promotion worker. ADR-023 §6, EPIC-A slice 3
  (#215). A future scheduler will call this on a cron / asyncio
  interval; for now manual trigger only.
* ``GET /admin/hitl/state`` — read-only snapshot of the HITL config
  + per-bucket SPC counters + drift ratios + effective sampling
  rates + the pending auto-promotion queue depth. Powers the Admin
  HITL dashboard (EPIC-A close-out, #215). Counter resets are out
  of scope per the no-delete policy; a future "vacuum" admin tool
  slices in.

Every archive route requires the ``admin`` role (ADR-019 §3 / #264) AND
``?confirm=true`` for non-dry-run mutating actions (defence in depth,
per ADR-027 §5). ``?dry_run=true`` returns the impact summary with no
state change and no audit row. The 410 Gone read response for purged
versions / fully-purged documents is wired in
:mod:`app.routes.lifecycle` (slice 6 of D.9).

The HITL auto-promotion route is admin-only too but does NOT use the
``?confirm=true`` defence-in-depth pattern: the pass is idempotent
(already-promoted rows are skipped, race-detected rows are skipped,
failed rows continue) and the side-effect (NEEDS_REVIEW → VALIDATED
on pre-decided rows) reflects what the router already chose, so a
second invocation cannot drift the catalog further than the first did.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response

from app.dependencies import PipelineServices, _resolve_llm_provider
from app.errors import ApiError, ErrorCode
from app.models.document import DocumentVersionStatus
from app.schemas.admin_archive import (
    ORBITAL_PURGE_ALL_PHRASE,
    ArchivedDocumentItem,
    ArchivedDocumentsResponse,
    OrbitalPurgeAllRequest,
    OrbitalPurgeAllResponse,
    OrbitalPurgeDocumentRequest,
    OrbitalPurgeDocumentResponse,
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
from app.schemas.admin_audit import (
    AdminAuditEventsResponse,
    AuditEventItem,
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
from app.schemas.admin_hitl import (
    AdminHITLStateResponse,
    BucketState,
)
from app.schemas.document import HealthResponse, ReadinessCheck, ReadyResponse
from app.schemas.scope import Scope
from app.schemas.validation_metadata import AutoPromoteResult
from app.services.audit_event_store import event_actor as _audit_event_actor
from app.services.auth import User, require_admin
from app.services.catalog_store import InvalidCursor
from app.services.knowledge.graph_store import Neo4jGraphStore
from app.services.knowledge.llm_client import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
)
from app.settings import Settings

log = logging.getLogger(__name__)


def _build_llm_config(settings: Settings) -> LLMConfig:
    """Project the LLM-provider-related settings onto the public response shape.

    Surfaces both providers' configured-flag + non-secret model id, plus
    the resolved ``active_provider`` per :func:`_resolve_llm_provider`
    so the Settings widget can show "Gemini in use, Anthropic available
    as fallback" without re-implementing the resolution rules.

    The legacy ``configured`` / ``model`` fields stay populated so any
    pre-amendment client keeps rendering. They mirror whichever
    provider is currently active.
    """
    active = _resolve_llm_provider(settings)
    if active == "gemini":
        active_model = settings.gemini_model.strip() or DEFAULT_GEMINI_MODEL
    elif active == "anthropic":
        active_model = settings.anthropic_model.strip() or DEFAULT_ANTHROPIC_MODEL
    else:
        active_model = ""
    return LLMConfig(
        configured=active is not None,
        model=active_model,
        max_input_tokens_per_document=settings.entity_extractor_max_input_tokens_per_document,
        provider_setting=settings.llm_provider,
        active_provider=active,
        gemini_configured=bool(settings.gemini_api_key),
        gemini_model=settings.gemini_model,
        anthropic_configured=bool(settings.anthropic_api_key),
        anthropic_model=settings.anthropic_model,
    )


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
        llm=_build_llm_config(settings),
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
            force_auto_corpus=settings.hitl_force_auto_corpus,
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
        "/ready",
        operation_id="ready",
        response_model=ReadyResponse,
        responses={503: {"model": ReadyResponse}},
    )
    def ready(response: Response) -> ReadyResponse:
        """Readiness probe — see :class:`ReadyResponse` for the contract.

        Required: the catalog answers a one-row read. Optional: when
        ``KW_KNOWLEDGE_LAYER_ENABLED=true`` AND the graph store is the
        Neo4j backend, ping it with ``RETURN 1``. Optional failures are
        reported but never gate readiness — the core review path keeps
        serving even when the knowledge-layer stack is degraded.
        """
        checks: dict[str, ReadinessCheck] = {}
        ready_overall = True

        try:
            services.documents.catalog.list_documents(limit=1)
            checks["catalog"] = ReadinessCheck(status="ok")
        except Exception as exc:  # noqa: BLE001 - readiness must not raise
            checks["catalog"] = ReadinessCheck(
                status="error",
                detail=str(exc)[:200],
            )
            ready_overall = False

        settings = Settings()
        if not settings.knowledge_layer_enabled:
            checks["neo4j"] = ReadinessCheck(status="disabled")
        elif isinstance(services.graph_store, Neo4jGraphStore):
            try:
                store = services.graph_store
                # Lightweight liveness ping — RETURN 1 is the cheapest
                # statement Neo4j accepts and proves both the driver and
                # the database session work.
                with store._driver.session(  # noqa: SLF001 - intentional probe
                    database=store._database,  # noqa: SLF001
                ) as session:
                    session.run("RETURN 1").consume()
                checks["neo4j"] = ReadinessCheck(status="ok")
            except Exception as exc:  # noqa: BLE001 - readiness must not raise
                checks["neo4j"] = ReadinessCheck(
                    status="error",
                    detail=str(exc)[:200],
                )
                # Optional dep — do NOT flip ready_overall.
        else:
            # Knowledge layer flag is on but the in-memory store is in use
            # (no Neo4j credentials configured). That is a valid posture
            # for local demos; report it so operators can see the gap
            # without it showing as an error.
            checks["neo4j"] = ReadinessCheck(
                status="disabled",
                detail="in-memory graph store; KW_NEO4J_* not configured",
            )

        if not ready_overall:
            response.status_code = 503
            return ReadyResponse(status="error", checks=checks)
        return ReadyResponse(status="ok", checks=checks)

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

    @router.post(
        "/admin/orbital/purge_document",
        operation_id="admin_orbital_purge_document",
        response_model=OrbitalPurgeDocumentResponse,
    )
    def orbital_purge_document(
        body: OrbitalPurgeDocumentRequest,
        confirm: bool = Query(False),
        user: User = Depends(require_admin),
    ) -> OrbitalPurgeDocumentResponse:
        """Hard-delete an active document from Orbital (#292 — operator override).

        Combines archive + ``purge_artifacts`` + KG subgraph cleanup
        in one audited call. The operator types the document's
        ``original_filename`` into the modal; the route 422s on a
        mismatch so a misclick can't take down the wrong family.

        Per the deletion-rules feedback (memory), Orbital is the only
        sanctioned hard-delete surface; every other entry point still
        flag-archives. The cascade order matches ADR-027:

        1. Archive the document (sets ``archived_at`` so all read
           paths immediately stop surfacing it).
        2. Purge each version's source artifacts (storage bytes +
           catalog ``storage_uri`` flip to a tombstone). Reuses
           :func:`_purge_one_document` so the per-version contract
           stays identical to the legacy admin path.
        3. Drop the KG subgraph for each version (best-effort —
           failures are logged + swallowed because KG is derived
           data, regenerable from the catalog).

        Audit emits a single ``orbital.document.purge`` event with
        the actor, filename, archive timestamp, and version count
        per the spec in #292.
        """
        if not confirm:
            raise ApiError(
                status_code=422,
                code=ErrorCode.UNPROCESSABLE_ENTITY,
                message="Orbital purge requires ?confirm=true.",
                retryable=False,
                remediation=(
                    "Append ?confirm=true to the request once the "
                    "operator has acknowledged the modal."
                ),
            )

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
                remediation="Verify the document id; archived rows are visible too.",
            )

        if body.confirmation_filename != document.original_filename:
            raise ApiError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message=(
                    "confirmation_filename does not match the document's "
                    f"original_filename ({document.original_filename!r})."
                ),
                retryable=False,
                remediation=(
                    "Type the document's filename exactly into the "
                    "Orbital purge modal — case-sensitive — to confirm."
                ),
            )

        archived_at = document.archived_at or datetime.now(UTC)
        if document.archived_at is None:
            catalog.flag_document_archived(
                body.document_id,
                archived_at=archived_at,
                actor=user.id,
            )

        purge_response = _purge_one_document(
            body.document_id,
            dry_run=False,
            actor=user.id,
            actor_role=user.role,
        )

        # KG cleanup — best-effort; KG is derived data, regenerable.
        kg_purged = False
        graph_store = getattr(services, "graph_store", None)
        if graph_store is not None:
            for version in document.versions:
                try:
                    graph_store.delete_subgraph_for_version(
                        document_id=body.document_id,
                        version_id=version.id,
                    )
                    kg_purged = True
                except Exception as exc:  # noqa: BLE001 — best-effort.
                    log.warning(
                        "orbital.document.purge.kg_delete_failed",
                        extra={
                            "document_id": body.document_id,
                            "version_id": version.id,
                            "error": str(exc),
                        },
                    )

        log.info(
            "orbital.document.purge",
            extra={
                "document_id": body.document_id,
                "original_filename": document.original_filename,
                "archived_at": archived_at.isoformat(),
                "versions_purged": len(purge_response.versions_purged),
                "kg_subgraph_purged": kg_purged,
                "actor": user.id,
                "actor_role": user.role,
            },
        )

        return OrbitalPurgeDocumentResponse(
            document_id=body.document_id,
            original_filename=document.original_filename,
            archived_at=archived_at,
            versions_purged=purge_response.versions_purged,
            kg_subgraph_purged=kg_purged,
        )

    def _orbital_purge_one(
        document_id: str,
        *,
        actor: str,
        actor_role: str,
    ) -> OrbitalPurgeDocumentResponse:
        """Bulk-friendly variant of the per-document Orbital cascade (#292).

        Same archive + purge_artifacts + KG cleanup + audit emission as
        :func:`orbital_purge_document`, but skips the
        ``confirmation_filename`` check (the bulk gate is the operator
        typing :data:`ORBITAL_PURGE_ALL_PHRASE` once at the modal —
        per-doc filenames aren't relevant when nuking the catalog).
        Returns the per-document response shape so the bulk wrapper
        can stitch them into its results list.
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
                remediation="Document missing during bulk purge — likely a concurrent change.",
            )

        archived_at = document.archived_at or datetime.now(UTC)
        if document.archived_at is None:
            catalog.flag_document_archived(
                document_id,
                archived_at=archived_at,
                actor=actor,
            )

        purge_response = _purge_one_document(
            document_id,
            dry_run=False,
            actor=actor,
            actor_role=actor_role,
        )

        kg_purged = False
        graph_store = getattr(services, "graph_store", None)
        if graph_store is not None:
            for version in document.versions:
                try:
                    graph_store.delete_subgraph_for_version(
                        document_id=document_id,
                        version_id=version.id,
                    )
                    kg_purged = True
                except Exception as exc:  # noqa: BLE001 — best-effort.
                    log.warning(
                        "orbital.document.purge.kg_delete_failed",
                        extra={
                            "document_id": document_id,
                            "version_id": version.id,
                            "error": str(exc),
                        },
                    )

        log.info(
            "orbital.document.purge",
            extra={
                "document_id": document_id,
                "original_filename": document.original_filename,
                "archived_at": archived_at.isoformat(),
                "versions_purged": len(purge_response.versions_purged),
                "kg_subgraph_purged": kg_purged,
                "actor": actor,
                "actor_role": actor_role,
            },
        )

        return OrbitalPurgeDocumentResponse(
            document_id=document_id,
            original_filename=document.original_filename,
            archived_at=archived_at,
            versions_purged=purge_response.versions_purged,
            kg_subgraph_purged=kg_purged,
        )

    @router.post(
        "/admin/orbital/purge_all",
        operation_id="admin_orbital_purge_all",
        response_model=OrbitalPurgeAllResponse,
    )
    def orbital_purge_all(
        body: OrbitalPurgeAllRequest,
        confirm: bool = Query(False),
        user: User = Depends(require_admin),
    ) -> OrbitalPurgeAllResponse:
        """Hard-delete every active document in the catalog (#292 §5 — bulk override).

        The user picked option 3 in #292 (Orbital is the sanctioned
        hard-delete surface) and explicitly asked for a bulk button.
        Two independent gates protect this path:

        1. ``?confirm=true`` query flag (matches the per-doc route).
        2. ``confirmation_phrase`` body field — must equal
           :data:`ORBITAL_PURGE_ALL_PHRASE` exactly (case-sensitive).
           A misclick stops at the modal; a malformed request stops
           at the server.

        Iterates every active document (``archived_at IS NULL``),
        cascades the same archive + purge_artifacts + KG cleanup as
        the per-doc route, and emits one ``orbital.document.purge``
        audit event per row plus a single
        ``orbital.knowledge_space.purge`` summary event with the total
        count + actor.

        Best-effort: a per-document failure is logged + recorded in
        ``failures`` but doesn't abort the batch.
        """
        if not confirm:
            raise ApiError(
                status_code=422,
                code=ErrorCode.UNPROCESSABLE_ENTITY,
                message="Orbital purge_all requires ?confirm=true.",
                retryable=False,
                remediation=(
                    "Append ?confirm=true to the request once the "
                    "operator has acknowledged the modal."
                ),
            )
        if body.confirmation_phrase != ORBITAL_PURGE_ALL_PHRASE:
            raise ApiError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message=(f"confirmation_phrase must equal {ORBITAL_PURGE_ALL_PHRASE!r}."),
                retryable=False,
                remediation=(
                    "Type the exact phrase shown in the Orbital purge-all "
                    "modal (case-sensitive) before submitting."
                ),
            )

        catalog = services.documents.catalog
        # Snapshot the ids of every active document upfront — the
        # per-doc cascade flips ``archived_at`` so iterating
        # ``list_documents`` mid-loop would skip rows.
        ids: list[str] = [doc.id for doc in catalog.list_documents()]

        results: list[OrbitalPurgeDocumentResponse] = []
        failures: list[str] = []
        for document_id in ids:
            try:
                results.append(
                    _orbital_purge_one(
                        document_id,
                        actor=user.id,
                        actor_role=user.role,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — best-effort, recorded.
                log.warning(
                    "orbital.knowledge_space.purge.doc_failed",
                    extra={
                        "document_id": document_id,
                        "error": str(exc),
                    },
                )
                failures.append(document_id)

        log.info(
            "orbital.knowledge_space.purge",
            extra={
                "documents_purged": len(results),
                "failed": len(failures),
                "actor": user.id,
                "actor_role": user.role,
            },
        )

        return OrbitalPurgeAllResponse(
            documents_purged=len(results),
            failed=len(failures),
            results=results,
            failures=failures,
        )

    @router.get(
        "/admin/archive/archived_documents",
        operation_id="admin_archive_list_archived",
        response_model=ArchivedDocumentsResponse,
    )
    def list_archived_documents(
        cursor: str | None = Query(None),
        limit: int = Query(50, ge=1, le=200),
        _user: User = Depends(require_admin),
    ) -> ArchivedDocumentsResponse:
        """Paginated read of flag-archived documents (D.9 admin UI).

        Returns one page of rows where ``archived_at IS NOT NULL``,
        sorted ``archived_at DESC`` (most-recently archived first) with
        ``id`` ASC as the tie-breaker. Each row carries the fields the
        admin UI needs to render its table without per-doc probes:

        - ``original_filename`` and ``archived_at`` for the heading.
        - ``last_active_scope_kind`` / ``last_active_scope_ref`` —
          derived from the most-recent soft-removed scope link on the
          document so the operator can see which scope was last
          removed before the cascade fired. ``None`` when no scope
          history is recoverable.
        - ``versions_purged`` / ``versions_remaining`` — split the
          version family by :data:`DocumentVersionStatus.PURGED` so
          the UI shows recoverable bytes vs already-tombstone'd bytes.

        Read-only: no ``?confirm=true`` / ``?dry_run=true``
        ceremony — the route doesn't mutate state.
        """
        try:
            documents, next_cursor = services.documents.catalog.list_archived_documents(
                cursor=cursor,
                limit=limit,
            )
        except InvalidCursor as exc:
            raise ApiError(
                status_code=400,
                code=ErrorCode.BAD_REQUEST,
                message=f"Invalid cursor: {exc}",
                retryable=False,
                remediation=(
                    "Drop the ``cursor`` query param to start at the "
                    "first page. The cursor format is opaque; do not "
                    "construct it client-side."
                ),
            ) from exc

        items: list[ArchivedDocumentItem] = []
        for document in documents:
            # Bucket versions by PURGED vs not so the UI can render the
            # "X / Y" recoverability hint without doing this math itself.
            versions_purged = 0
            versions_remaining = 0
            for version in document.versions:
                if version.status is DocumentVersionStatus.PURGED:
                    versions_purged += 1
                else:
                    versions_remaining += 1

            # The most-recent soft-removed scope link is the proxy for
            # "the scope that was removed before the cascade archived
            # the document". The store leaks soft-removed rows on
            # ``Document.scopes`` for archived listings (the active
            # filter would hide exactly the rows we want here). When
            # no scope history is recoverable — never-scoped doc, or
            # scope rows missing — both fields fall back to None and
            # the UI renders a "—" placeholder.
            last_active_scope_kind: str | None = None
            last_active_scope_ref: str | None = None
            removed_links = [s for s in document.scopes if s.removed_at is not None]
            if removed_links:
                # ``removed_at`` is the wall clock the cascade stamped
                # on the link; the most recent removal is the one
                # immediately preceding the archive.
                latest_removed = max(
                    removed_links,
                    key=lambda s: s.removed_at,  # type: ignore[arg-type, return-value]
                )
                last_active_scope_kind = latest_removed.kind
                last_active_scope_ref = latest_removed.ref

            assert document.archived_at is not None  # store guarantees
            items.append(
                ArchivedDocumentItem(
                    document_id=document.id,
                    original_filename=document.original_filename,
                    archived_at=document.archived_at,
                    last_active_scope_kind=last_active_scope_kind,
                    last_active_scope_ref=last_active_scope_ref,
                    versions_purged=versions_purged,
                    versions_remaining=versions_remaining,
                )
            )

        return ArchivedDocumentsResponse(items=items, next_cursor=next_cursor)

    @router.post(
        "/admin/hitl/run_auto_promote_pass",
        operation_id="admin_hitl_run_auto_promote_pass",
        response_model=AutoPromoteResult,
    )
    def run_auto_promote_pass(
        max_versions: int | None = Query(
            None,
            ge=1,
            le=1000,
            description=(
                "Optional cap on the number of pending versions the "
                "pass touches. Pass ``null`` (omit the param) to "
                "process every pending row. Bounded ``[1, 1000]`` to "
                "keep the synchronous request shape responsive — a "
                "real scheduler will pick the right batch size when "
                "it lands."
            ),
        ),
        user: User = Depends(require_admin),
    ) -> AutoPromoteResult:
        """Run one HITL auto-promotion pass (ADR-023 §6, slice 3, #215).

        Synchronous: the worker runs in-line within the request and
        the structured :class:`AutoPromoteResult` is returned directly
        so operators see the full per-version outcome without grepping
        logs. A future cron scheduler will call this same route on an
        interval; for now manual trigger.

        Returns 503 with ``KW_HITL_DISABLED`` (mirrored on the route's
        admin gate) when the auto-promoter is not wired —
        ``KW_HITL_DISABLE_SCORER=true`` disables the scorer, the router
        and (transitively) the worker. The empty result is also a
        valid response: an admin clicking the button when no rows are
        pending sees ``scanned=0, promoted=[], skipped=[], failed=[]``
        and a 200.
        """
        if services.hitl_auto_promoter is None:
            raise ApiError(
                status_code=503,
                code=ErrorCode.HITL_DISABLED,
                message=(
                    "HITL auto-promotion worker is not wired. "
                    "Likely cause: KW_HITL_DISABLE_SCORER=true."
                ),
                retryable=False,
                remediation=(
                    "Unset KW_HITL_DISABLE_SCORER (or set it to false) "
                    "and restart the API. The router + worker share the "
                    "same kill switch as the scorer."
                ),
            )
        log.info(
            "admin.hitl.run_auto_promote_pass.invoked",
            extra={
                "actor": user.id,
                "actor_role": user.role,
                "max_versions": max_versions,
            },
        )
        return services.hitl_auto_promoter.run_pass(max_versions=max_versions)

    @router.get(
        "/admin/hitl/state",
        operation_id="admin_hitl_get_state",
        response_model=AdminHITLStateResponse,
    )
    def get_hitl_state(
        _user: User = Depends(require_admin),
    ) -> AdminHITLStateResponse:
        """Read-only snapshot of HITL routing state (EPIC-A close-out, #215).

        Surfaces three things the operator needs to see at a glance:

        1. **Config posture** — the env-driven knobs the router and
           drift detector were constructed with (threshold, baseline
           sample rate, drift threshold + ramp factor, plus the
           force-auto and scorer kill switches).
        2. **Per-bucket SPC counters** — every bucket the router has
           recorded a decision against, decorated with its drift
           ratio and the drift detector's *current* effective sample
           rate. Sorted by ``drift_ratio`` DESC so the noisiest
           buckets surface at the top of the dashboard table.
        3. **Pending auto-promotion queue depth** — the count of
           rows the next ``run_auto_promote_pass`` invocation would
           touch, so the dashboard can render the queue size next to
           its trigger button without a second probe.

        Returns 503 with ``KW_HITL_DISABLED`` (mirrored on the
        auto-promote-pass route) when ``KW_HITL_DISABLE_SCORER`` is
        truthy — a disabled scorer means the router is also unwired
        and the snapshot would be misleading. Read-only: no
        ``?confirm=true`` ceremony.

        Per ADR-023 §6 and EPIC-A's "auto-validated == human-validated
        to consumers" rule, the dashboard never exposes individual
        :class:`ValidationMetadata` rows — the metadata stays internal.
        """
        # Re-read settings on every request so ``monkeypatch.setenv``
        # in tests is observed without restarting the app — same
        # posture the existing ``/admin/config`` route uses.
        settings = Settings()
        if services.hitl_router is None:
            # Tied kill-switch: scorer disabled => router/auto-promoter
            # both None. The snapshot would report all-zero buckets
            # and a stale config; failing fast is more honest.
            raise ApiError(
                status_code=503,
                code=ErrorCode.HITL_DISABLED,
                message=("HITL routing is not wired. Likely cause: KW_HITL_DISABLE_SCORER=true."),
                retryable=False,
                remediation=(
                    "Unset KW_HITL_DISABLE_SCORER (or set it to false) "
                    "and restart the API. The router + auto-promoter + "
                    "dashboard share the same kill switch as the scorer."
                ),
            )

        baseline_rate = settings.hitl_spc_sample_rate
        drift_threshold = settings.hitl_drift_threshold
        ramp_factor = settings.hitl_drift_ramp_factor

        bucket_states: list[BucketState] = []
        for sampling_bucket, counters in services.sampling_state.list_all_buckets():
            # ``max(_, 1)`` floors the denominator so a bucket with no
            # auto decisions yet doesn't blow up the response — the
            # detector itself returns the baseline in that case.
            denominator = max(counters.samples_auto, 1)
            drift_ratio = counters.samples_human_after_auto / denominator
            # Pre-cold-start (samples_auto == 0) the detector returns
            # the baseline. Compute via the bucket's same logic so the
            # dashboard matches what the router will see on the next
            # decision.
            if counters.samples_auto == 0 or drift_ratio <= drift_threshold:
                effective_rate = baseline_rate
            else:
                effective_rate = min(1.0, baseline_rate * ramp_factor)
            bucket_states.append(
                BucketState(
                    content_type=sampling_bucket.content_type,
                    topic_cluster=sampling_bucket.topic_cluster,
                    samples_taken=counters.samples_taken,
                    samples_auto=counters.samples_auto,
                    samples_human=counters.samples_human,
                    samples_human_after_auto=counters.samples_human_after_auto,
                    drift_ratio=drift_ratio,
                    effective_sample_rate=effective_rate,
                    last_decision_at=counters.last_decision_at,
                )
            )

        # Sort hot-spots first so an admin scanning the table catches
        # drifting buckets without scrolling. Tie-break on
        # ``samples_taken`` DESC then bucket key for determinism.
        bucket_states.sort(
            key=lambda b: (
                -b.drift_ratio,
                -b.samples_taken,
                b.content_type,
                b.topic_cluster,
            )
        )

        pending = len(services.validation_metadata.list_pending_auto_promotions())

        return AdminHITLStateResponse(
            enabled=not settings.hitl_scorer_disabled,
            force_auto_corpus=settings.hitl_force_auto_corpus,
            threshold=settings.hitl_auto_validate_threshold,
            baseline_sample_rate=baseline_rate,
            drift_threshold=drift_threshold,
            drift_ramp_factor=ramp_factor,
            pending_auto_promotions=pending,
            buckets=bucket_states,
        )

    @router.get(
        "/admin/audit/events",
        operation_id="admin_audit_list_events",
        response_model=AdminAuditEventsResponse,
    )
    def list_audit_events(
        event_name: str | None = Query(
            None,
            description=(
                "Restrict results to a single dotted event name "
                "(e.g. ``review.validated``). The full vocabulary is "
                "surfaced on the response's ``available_event_names`` "
                "so the UI dropdown is self-populating."
            ),
        ),
        actor: str | None = Query(
            None,
            description=(
                "Restrict results to events emitted by a specific "
                "principal — matches the ``actor`` field projected "
                "out of the structured-logging payload (the admin "
                "routes stash ``actor=user.id``). Rows with no actor "
                "are excluded only when this filter is set."
            ),
        ),
        since: datetime | None = Query(
            None,
            description=(
                "Lower-bound timestamp (inclusive). Events with ``created_at < since`` are skipped."
            ),
        ),
        until: datetime | None = Query(
            None,
            description=(
                "Upper-bound timestamp (inclusive). Events with ``created_at > until`` are skipped."
            ),
        ),
        cursor: str | None = Query(
            None,
            description=(
                "Opaque cursor returned in a prior response's "
                "``next_cursor``. Pass it to advance pages within the "
                "current filter set; drop it to start over."
            ),
        ),
        limit: int = Query(
            50,
            ge=1,
            le=200,
            description=(
                "Page size. Defaults to 50 to keep the dashboard "
                "responsive; the upper bound mirrors the audit "
                "store's ``MAX_QUERY_LIMIT`` so an over-eager filter "
                "can't drag back the entire table."
            ),
        ),
        _user: User = Depends(require_admin),
    ) -> AdminAuditEventsResponse:
        """Paginated read of the structured audit event log (#206 follow-up).

        The viewer is read-only — the audit table is append-only by
        design. Events sort by ``created_at DESC`` so the freshest
        rows surface at the top of the operator's table; ``cursor``
        encodes the page boundary opaquely so the same-timestamp tie
        case paginates cleanly across both store impls.

        Returns 503 with ``KW_AUDIT_DISABLED`` when
        ``KW_AUDIT_ENABLED=false`` (the in-memory default). The store
        still works in-process for live event capture but a
        deployment that opts out of the persistent DB has no
        historical rows to browse, so the route fails closed with a
        remediation hint pointing at the env var.

        ``available_event_names`` is included on every response so
        the UI's filter dropdown can be self-populating without a
        second probe — cheap by construction since the audit table
        indexes ``event_name`` directly.
        """
        # Re-read settings on every request so ``monkeypatch.setenv``
        # in tests is observed without restarting the app — same
        # posture every other admin route uses.
        settings = Settings()
        if not settings.audit_enabled:
            raise ApiError(
                status_code=503,
                code=ErrorCode.AUDIT_DISABLED,
                message=(
                    "Audit log is disabled. Likely cause: "
                    "KW_AUDIT_ENABLED=false (the in-memory default)."
                ),
                retryable=False,
                remediation=(
                    "Set KW_AUDIT_ENABLED=true (and optionally "
                    "KW_AUDIT_DB_PATH=/path/to/audit.sqlite3 for a "
                    "persistent deployment) and restart the API."
                ),
            )

        try:
            rows, next_cursor = services.audit_events.query_page(
                event_name=event_name,
                actor=actor,
                since=since,
                until=until,
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            raise ApiError(
                status_code=400,
                code=ErrorCode.BAD_REQUEST,
                message=f"Invalid cursor: {exc}",
                retryable=False,
                remediation=(
                    "Drop the ``cursor`` query param to start at the "
                    "first page. The cursor format is opaque; do not "
                    "construct it client-side."
                ),
            ) from exc

        items: list[AuditEventItem] = []
        for event in rows:
            row_actor = _audit_event_actor(event)
            ts_iso = event.ts_utc.astimezone(UTC).isoformat(timespec="seconds")
            items.append(
                AuditEventItem(
                    # Synthesised stable id for the React key + a11y
                    # row anchor. Opaque to clients.
                    id=f"{ts_iso}:{event.event_name}:{row_actor or '-'}",
                    event_name=event.event_name,
                    actor=row_actor,
                    created_at=event.ts_utc,
                    payload=dict(event.payload),
                )
            )

        return AdminAuditEventsResponse(
            items=items,
            next_cursor=next_cursor,
            available_event_names=services.audit_events.list_event_names(),
        )

    return router
