import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import PipelineServices, build_persistent_services, build_services
from app.errors import install_error_handlers
from app.logging_config import configure_logging, install_audit_handler
from app.routes import build_router
from app.services.catalog_backup import prune_old_snapshots, snapshot_catalog
from app.services.extraction_recovery import recover_stuck_extractions
from app.services.extraction_worker import (
    ExtractionWorker,
    InMemoryExtractionQueue,
)
from app.services.knowledge.graph_store import VECTOR_INDEX_NAME
from app.services.neo4j_backup import (
    prune_old_neo4j_snapshots,
    snapshot_neo4j,
)
from app.settings import Settings

log = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    """Parse the CORS allowlist from the typed settings model.

    Returns an empty list when the variable is unset or blank, which means the
    API responds to no cross-origin requests until an operator opts in.
    Reads are routed through :class:`app.settings.Settings` (issue #43);
    ``KW_CORS_ALLOWED_ORIGINS`` is the canonical name and the legacy
    ``CORS_ALLOWED_ORIGINS`` keeps working as a Pydantic alias.
    """
    return Settings().cors_allowed_origins


def _allowed_origin_regex() -> str | None:
    """Parse the optional regex CORS allowlist.

    Returns ``None`` when the env var is unset or blank, which keeps
    Starlette's ``CORSMiddleware`` on the exact-allowlist path. A
    non-empty value (e.g. ``^https://.*\\.3dexperience\\.3ds\\.com$``)
    is forwarded verbatim to ``allow_origin_regex`` so the deployed
    backend can accept whole tenant families without enumerating every
    subdomain in the CSV allowlist.
    """
    raw = Settings().cors_allowed_origin_regex.strip()
    return raw or None


@asynccontextmanager
async def _extraction_worker_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Async-mode startup/shutdown for the extraction queue (ADR-006).

    On startup:
    - Run the boot-time stuck-state scan (always, but a no-op when
      ``extraction_inline=True`` so the inline default pays nothing).
    - When ``extraction_inline=False``, build the in-process queue and
      spawn ``extraction_workers`` worker tasks attached to the running
      event loop.

    On shutdown: cancel every worker task and await its exit so the
    process stops cleanly.

    The harness is dormant under ``extraction_inline=True`` (PR-1
    default): no queue, no tasks, no behavior change for the existing
    test suite or demo posture.
    """
    services: PipelineServices = app.state.services
    settings: Settings = services.settings

    # Tracker for fire-and-forget validation side-effects when
    # ``KW_KNOWLEDGE_PROJECTION_ASYNC=true`` is on. Holding strong
    # references prevents the GC from reaping running tasks; the
    # ``add_done_callback(discard)`` in the dispatcher cleans up
    # finished ones. Initialized unconditionally so the route layer
    # can rely on the attribute existing.
    app.state.background_tasks = set()

    # Boot-time recovery — runs unconditionally (helper short-circuits
    # under inline mode internally so we don't double-gate it here).
    recover_stuck_extractions(services)

    # Periodic catalog backup runs independent of the worker mode —
    # data loss is just as bad in inline mode. The helper is a no-op
    # under the in-memory wiring (no SQLite file to copy), so it's
    # safe to spawn unconditionally.
    backup_task: asyncio.Task[None] | None = None
    if settings.backup_interval_seconds > 0:
        backup_task = asyncio.create_task(
            _periodic_catalog_backup(
                services,
                interval_seconds=settings.backup_interval_seconds,
                retain=settings.backup_retain_count,
            ),
            name="catalog-backup",
        )
    app.state.catalog_backup_task = backup_task

    # #381: parallel Neo4j backup task. Disabled by default; operator
    # opts in via ``KW_NEO4J_BACKUP_INTERVAL_SECONDS > 0``. Boot fails
    # below if the interval is set but the destination directory is
    # missing — silent disablement is the failure mode this option
    # exists to prevent.
    neo4j_backup_task: asyncio.Task[None] | None = None
    if settings.neo4j_backup_interval_seconds > 0:
        if not (settings.neo4j_backup_dir or "").strip():
            raise RuntimeError(
                "KW_NEO4J_BACKUP_INTERVAL_SECONDS is set but "
                "KW_NEO4J_BACKUP_DIR is empty. Configure a writable "
                "directory for the Neo4j backup task or set the "
                "interval to 0 to disable. See ADR-031 for the "
                "disaster-recovery rationale."
            )
        neo4j_backup_task = asyncio.create_task(
            _periodic_neo4j_backup(
                settings,
                interval_seconds=settings.neo4j_backup_interval_seconds,
                retain=settings.neo4j_backup_retain_count,
            ),
            name="neo4j-backup",
        )
    app.state.neo4j_backup_task = neo4j_backup_task

    workers: list[ExtractionWorker] = []
    recovery_task: asyncio.Task[None] | None = None
    if not settings.extraction_inline:
        queue = InMemoryExtractionQueue(maxsize=settings.extraction_queue_size)
        for i in range(settings.extraction_workers):
            worker = ExtractionWorker(
                queue=queue,
                jobs=services.extraction_jobs,
                name=f"extraction-worker-{i}",
            )
            await worker.start()
            workers.append(worker)
        app.state.extraction_queue = queue
        app.state.extraction_workers = workers
        log.info(
            "extraction.worker_pool.started",
            extra={
                "worker_count": len(workers),
                "queue_size": settings.extraction_queue_size,
            },
        )
        # Periodic stuck-state recovery. Without it, a single transient
        # worker failure can leave a doc in QUEUED_FOR_EXTRACTION /
        # EXTRACTING until the next manual restart — exactly the kind
        # of state operators end up bouncing the process to clear.
        interval = settings.extraction_recovery_interval_seconds
        if interval > 0:
            recovery_task = asyncio.create_task(
                _periodic_stuck_extraction_recovery(services, interval),
                name="extraction-recovery",
            )
            app.state.extraction_recovery_task = recovery_task
        else:
            app.state.extraction_recovery_task = None
    else:
        # Inline mode: leave the attributes unset so callers that try
        # to enqueue receive an explicit ``AttributeError`` rather than
        # silently dropping jobs into the void. PR-2's route shim will
        # check ``settings.extraction_inline`` first and route accordingly.
        app.state.extraction_queue = None
        app.state.extraction_workers = []
        app.state.extraction_recovery_task = None

    try:
        yield
    finally:
        if recovery_task is not None:
            recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await recovery_task
        if backup_task is not None:
            backup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await backup_task
        if neo4j_backup_task is not None:
            neo4j_backup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await neo4j_backup_task
        await _drain_background_tasks(
            app.state.background_tasks,
            timeout_seconds=settings.background_task_shutdown_timeout_seconds,
        )
        for worker in workers:
            await worker.stop()


async def _drain_background_tasks(
    tasks: set[asyncio.Task[None]],
    *,
    timeout_seconds: float,
) -> None:
    """Wait for in-flight async validation side-effects to finish.

    Bounded so a stuck Anthropic / Voyage call cannot hold container
    shutdown forever. Anything still running past ``timeout_seconds``
    is cancelled and the count is logged. ``timeout_seconds=0``
    cancels immediately (no graceful wait).
    """
    if not tasks:
        return
    pending = list(tasks)
    if timeout_seconds <= 0:
        for task in pending:
            task.cancel()
        log.info(
            "knowledge.projection.background_tasks_cancelled_at_shutdown",
            extra={"cancelled_count": len(pending), "timeout_seconds": timeout_seconds},
        )
        return

    done, still_pending = await asyncio.wait(pending, timeout=timeout_seconds)
    if still_pending:
        for task in still_pending:
            task.cancel()
        log.warning(
            "knowledge.projection.background_tasks_timed_out_on_shutdown",
            extra={
                "drained_count": len(done),
                "cancelled_count": len(still_pending),
                "timeout_seconds": timeout_seconds,
            },
        )


def _run_one_backup_cycle(services: PipelineServices, retain: int) -> str:
    """Snapshot the catalog once and prune old files. Pure-sync helper.

    Returns one of:

    - ``"ok"``: snapshot written; the caller should keep looping.
    - ``"in_memory"``: the catalog has no file to back up. The caller
      should stop looping (no point retrying — the wiring won't change
      mid-process).
    - ``"error"``: the snapshot failed. The caller should keep looping
      so a transient I/O blip doesn't disable backups for the rest of
      the process lifetime.

    Extracted from the asyncio loop so the body is testable without
    needing to wait on ``asyncio.sleep`` ticks. The loop scaffold stays
    thin (sleep, dispatch, repeat) and exception-isolated.
    """
    try:
        dest = snapshot_catalog(services)
    except Exception as exc:  # noqa: BLE001 - never let the loop die
        log.warning(
            "catalog_backup.snapshot_failed",
            extra={"error_type": type(exc).__name__},
        )
        return "error"

    if dest is None:
        log.info(
            "catalog_backup.skipped",
            extra={"reason": "in-memory catalog; nothing to back up"},
        )
        return "in_memory"

    log.info(
        "catalog_backup.snapshot_completed",
        extra={"path": str(dest)},
    )

    try:
        pruned = prune_old_snapshots(dest.parent, retain=retain)
    except Exception as exc:  # noqa: BLE001 - prune failure ≠ backup failure
        log.warning(
            "catalog_backup.prune_failed",
            extra={"error_type": type(exc).__name__},
        )
        return "ok"

    if pruned:
        log.info(
            "catalog_backup.pruned",
            extra={"pruned_count": len(pruned)},
        )
    return "ok"


async def _periodic_catalog_backup(
    services: PipelineServices,
    *,
    interval_seconds: int,
    retain: int,
) -> None:
    """Sleep ``interval_seconds`` and run one backup cycle, forever.

    Stops the loop when the cycle reports ``"in_memory"`` (no SQLite
    file to back up — the wiring is fixed for the lifetime of the
    process, so retrying is pointless). Continues on ``"ok"`` or
    ``"error"``.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            outcome = await asyncio.to_thread(_run_one_backup_cycle, services, retain)
        except asyncio.CancelledError:  # pragma: no cover - cancellation race
            return
        if outcome == "in_memory":
            return


def _run_one_neo4j_backup_cycle(settings: Settings, retain: int) -> str:
    """Snapshot Neo4j once and prune old dump subdirectories. Pure-sync (#381).

    Returns ``"ok"`` on success, ``"error"`` on a failed dump (the
    loop continues so a transient failure doesn't disable backups).
    Audit emission is structured per ADR-027 conventions:
    ``ops.neo4j_backup.completed`` on success,
    ``ops.neo4j_backup.failed`` on dump failure. Operators scrape
    these via the existing ``GET /admin/audit/events`` route.
    """
    import subprocess

    try:
        dest = snapshot_neo4j(settings)
    except subprocess.CalledProcessError as exc:
        log.warning(
            "ops.neo4j_backup.failed",
            extra={
                "command": " ".join(exc.cmd) if exc.cmd else "",
                "returncode": exc.returncode,
                "stderr": (exc.stderr or "")[:500],
                "error_type": "CalledProcessError",
            },
        )
        return "error"
    except Exception as exc:  # noqa: BLE001 - never let the loop die
        log.warning(
            "ops.neo4j_backup.failed",
            extra={"error_type": type(exc).__name__},
        )
        return "error"

    if dest is None:
        # ``KW_NEO4J_BACKUP_DIR`` is empty. The lifespan boot guard
        # prevents this in production wiring; defensive log here for
        # the off-chance a test path lands here.
        log.warning(
            "ops.neo4j_backup.skipped",
            extra={"reason": "no destination configured"},
        )
        return "error"

    log.info(
        "ops.neo4j_backup.completed",
        extra={"path": str(dest)},
    )

    try:
        pruned = prune_old_neo4j_snapshots(dest.parent, retain=retain)
    except Exception as exc:  # noqa: BLE001 - prune failure ≠ backup failure
        log.warning(
            "ops.neo4j_backup.prune_failed",
            extra={"error_type": type(exc).__name__},
        )
        return "ok"

    if pruned:
        log.info(
            "ops.neo4j_backup.pruned",
            extra={"pruned_count": len(pruned)},
        )
    return "ok"


async def _periodic_neo4j_backup(
    settings: Settings,
    *,
    interval_seconds: int,
    retain: int,
) -> None:
    """Sleep ``interval_seconds`` and run one Neo4j backup cycle, forever.

    Mirrors :func:`_periodic_catalog_backup` for the SQLite catalog
    (#357). Always continues on a failed cycle — a stuck Neo4j or a
    transient ``neo4j-admin`` blip should not silently disable
    backups for the rest of the process lifetime.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            await asyncio.to_thread(_run_one_neo4j_backup_cycle, settings, retain)
        except asyncio.CancelledError:  # pragma: no cover - cancellation race
            return


def _run_one_stuck_extraction_recovery(services: PipelineServices) -> None:
    """Run one stuck-extraction recovery scan. Pure-sync helper.

    Same extraction motivation as ``_run_one_backup_cycle``: lift the
    body out of the asyncio loop so coverage tests can drive it
    without sleeping.
    """
    try:
        recovered = recover_stuck_extractions(services)
    except Exception as exc:  # noqa: BLE001 - never let the loop die
        log.warning(
            "extraction.recovery.periodic_scan_failed",
            extra={"error_type": type(exc).__name__},
        )
        return
    if recovered:
        log.info(
            "extraction.recovery.periodic_scan_recovered",
            extra={"recovered_count": recovered},
        )


async def _periodic_stuck_extraction_recovery(
    services: PipelineServices,
    interval_seconds: int,
) -> None:
    """Sleep ``interval_seconds`` and run one recovery scan, forever."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            await asyncio.to_thread(_run_one_stuck_extraction_recovery, services)
        except asyncio.CancelledError:  # pragma: no cover - cancellation race
            return


def create_app(
    services: PipelineServices | None = None,
    *,
    persistent: bool = False,
    data_dir: str = ".kw-pipeline",
) -> FastAPI:
    """Create a Harvester API app with isolated pipeline services."""
    app = FastAPI(
        title="KW Pipeline Harvester API",
        version="0.1.0",
        description=(
            "Auditable document-intelligence pipeline. Endpoints cover "
            "upload, hashing, duplicate detection, parsing, semantic-JSON "
            "generation, reviewer validate/reject, and an optional "
            "knowledge-graph projection (ADR-012). Every claim and edge "
            "carries provenance via `source_reference_id`. "
            "See `docs/architecture/document_intelligence_mvp.md` for the "
            "ingestion contract and `docs/architecture/api_contract.md` "
            "for the public response shapes."
        ),
        contact={
            "name": "KW Pipeline",
            "url": "https://github.com/SofianeBENHELLI/KW-Pipeline",
        },
        license_info={
            "name": "Proprietary — all rights reserved",
            "url": "https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/LICENSE",
        },
        lifespan=_extraction_worker_lifespan,
    )
    if services is None:
        services = build_persistent_services(data_dir) if persistent else build_services()

    # Install the structured-logging handler once per app instance
    # (issue #42). ``configure_logging`` is idempotent — replacing the
    # root handler — so test suites that build many ``create_app``
    # instances in one process don't produce duplicate log lines.
    configure_logging(services.settings)

    # Audit handler (#26 residual) — persists every dotted-name
    # structured event into ``services.audit_events``. Idempotent: a
    # previous handler bound to the same store is removed first so
    # rebuilding the app doesn't stack duplicate handlers in the same
    # process. The handler is always attached; the store choice (in-
    # memory vs SQLite) decides whether anything actually persists.
    install_audit_handler(services.audit_events)

    app.state.services = services
    cors_kwargs: dict[str, Any] = {
        "allow_origins": _allowed_origins(),
        "allow_credentials": False,
        "allow_methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["*"],
    }
    origin_regex = _allowed_origin_regex()
    if origin_regex is not None:
        cors_kwargs["allow_origin_regex"] = origin_regex
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # Issue #120 — wrap raised HTTPExceptions into the public error
    # envelope defined in ``app.errors``. The legacy ``detail`` field is
    # preserved alongside ``error.{code,message,status}`` so existing
    # clients and tests don't break.
    install_error_handlers(app)

    app.include_router(build_router(services))

    _ensure_vector_index(services)

    return app


def _ensure_vector_index(services: PipelineServices) -> None:
    """Provision the Phase 3 chunk-embedding vector index on startup.

    Runs only when both gates are on (knowledge layer enabled + Voyage
    configured); otherwise the search service is ``None`` and the
    route returns 503. Failures are logged and swallowed so a Neo4j
    blip during boot does not stop the API from accepting Phase 1 /
    Phase 2 traffic.
    """
    if services.embedding_client is None:
        return
    try:
        services.graph_store.ensure_vector_index(
            name=VECTOR_INDEX_NAME,
            dim=services.embedding_client.dim,
        )
        log.info(
            "knowledge.vector_index.created",
            extra={
                "index_name": VECTOR_INDEX_NAME,
                "dim": services.embedding_client.dim,
                "embedding_model": services.embedding_client.name,
                "store": getattr(services.graph_store, "name", "unknown"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - fire-and-log boundary
        log.warning(
            "knowledge.vector_index.failed",
            extra={
                "index_name": VECTOR_INDEX_NAME,
                "embedding_model": services.embedding_client.name,
                "error_type": type(exc).__name__,
            },
        )


def _build_app() -> FastAPI:
    """Pick in-memory vs persistent wiring based on the env-driven settings.

    Used only for the module-level ``app`` symbol that uvicorn imports
    via ``app.main:app`` (issue #130 — demo MVP startup path). The
    programmatic ``create_app(persistent=True)`` route the test suite
    and ``docs/architecture/persistence.md`` exercise is unchanged: this
    helper exists exclusively so a presenter can flip ``KW_PERSISTENT=true``
    in the environment instead of editing Python.
    """
    settings = Settings()
    return create_app(
        persistent=settings.persistent,
        data_dir=settings.data_dir,
    )


app = _build_app()
