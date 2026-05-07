"""In-process async extraction queue + worker (ADR-006, #40 PR-1).

The worker harness runs as one or more asyncio tasks attached to the
FastAPI event loop. Submission is non-blocking; execution happens off
the event loop via :meth:`asyncio.AbstractEventLoop.run_in_executor`
because ``pdfplumber`` and ``python-docx`` are synchronous CPU/IO-blocking
calls.

PR-1 ships the harness only — no route wiring, no FSM bump. The route
handler keeps calling :class:`ExtractionJobService` synchronously while
``Settings.extraction_inline`` defaults to ``True``. PR-2 swaps the
route to enqueue here; PR-3 flips the flag default.

Two seams matter for the Postgres-trajectory ADR-022 follow-up:

- :class:`ExtractionQueue` is a :class:`Protocol` so a future
  Postgres-backed queue (``SELECT … FOR UPDATE SKIP LOCKED``) can drop
  in without touching the worker code.
- :class:`ExtractionWorker` consumes ``ExtractionRequest`` value
  objects, not raw tuples, so adding fields (priority, deadline,
  workspace scope from ADR-020) doesn't break worker callers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.extraction_job_service import ExtractionJobService

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    """Value object enqueued by the route, dequeued by the worker.

    Identifies a single ``(document_id, version_id)`` extraction job.
    Frozen so the worker can't mutate it mid-flight; ``slots=True`` keeps
    the payload tight in memory for high-throughput queues.
    """

    document_id: str
    version_id: str


class QueueFull(Exception):
    """Raised when an :class:`ExtractionQueue` is at capacity.

    PR-2 will translate this into a 503 ``Retry-After`` envelope. PR-1
    only exposes it on the queue surface.
    """


class ExtractionQueue(Protocol):
    """Persistence boundary for the extraction job queue.

    The in-memory implementation in this module is the MVP fit
    (ADR-006 §1). When ADR-022 lands and the persistence trajectory
    moves to Postgres, a second implementation can swap in without
    touching :class:`ExtractionWorker`.
    """

    @property
    def maxsize(self) -> int: ...

    def qsize(self) -> int: ...

    def is_full(self) -> bool: ...

    async def put(self, request: ExtractionRequest) -> None:
        """Enqueue a job. Must raise :class:`QueueFull` when at capacity.

        The MVP impl never blocks even when the queue is full — backpressure
        is signalled to the caller (the route) so it can return 503 to the
        client immediately rather than hold the request thread indefinitely.
        """

    async def get(self) -> ExtractionRequest:
        """Block until a job is available and return it.

        Cancellation propagates: when the worker task is cancelled mid-await,
        the exception bubbles up unchanged so the run loop exits cleanly.
        """

    async def close(self) -> None:
        """Release queue resources. Idempotent. Always safe to call from
        a shutdown hook."""


class InMemoryExtractionQueue:
    """Bounded in-memory queue backed by :class:`asyncio.Queue`.

    Lives in process memory; a restart drops every queued job. The
    boot-time stuck-state recovery in :func:`recover_stuck_extractions`
    is the safety net for that — versions left mid-flight surface to
    the operator as ``FAILED`` with a clear reason and ride the
    existing retry-extraction path back to a healthy state (ADR-006 §5).
    """

    def __init__(self, *, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize}")
        self._queue: asyncio.Queue[ExtractionRequest] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def qsize(self) -> int:
        return self._queue.qsize()

    def is_full(self) -> bool:
        return self._queue.full()

    async def put(self, request: ExtractionRequest) -> None:
        try:
            self._queue.put_nowait(request)
        except asyncio.QueueFull as exc:
            raise QueueFull(
                f"Extraction queue is at capacity ({self._maxsize}). Retry shortly."
            ) from exc

    async def get(self) -> ExtractionRequest:
        return await self._queue.get()

    async def close(self) -> None:
        # asyncio.Queue has no close API; the GC reclaims it once all
        # references drop. The method exists so a future persistent
        # implementation can flush state symmetrically.
        return None


class ExtractionWorker:
    """One asyncio task that drains an :class:`ExtractionQueue`.

    Construction stores the dependencies; :meth:`start` schedules the
    background task on the running event loop. Calling :meth:`stop`
    cancels the task and awaits its exit so shutdown is deterministic.

    The worker delegates parser execution to the existing synchronous
    :class:`ExtractionJobService` via
    :meth:`asyncio.AbstractEventLoop.run_in_executor`. This keeps the
    FSM logic in one place and lets the loop accept HTTP traffic while
    a long pdfplumber call grinds through.

    PR-1 does NOT wire any caller into the queue — :meth:`start` /
    :meth:`stop` are exercised by the lifespan hook in :mod:`app.main`,
    and the unit tests drive the queue directly. PR-2 will add the
    route-side ``put`` plumbing.
    """

    def __init__(
        self,
        *,
        queue: ExtractionQueue,
        jobs: ExtractionJobService,
        name: str = "extraction-worker",
    ) -> None:
        self._queue = queue
        self._jobs = jobs
        self._name = name
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Schedule the run loop on the current event loop. Idempotent."""
        if self.running:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name=self._name)
        log.info("extraction.worker.started", extra={"worker_name": self._name})

    async def stop(self) -> None:
        """Cancel the run loop and await its exit. Idempotent.

        Safe to call from a lifespan shutdown hook even if :meth:`start`
        was never called or the worker has already exited.
        """
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._stopped.set()
        log.info("extraction.worker.stopped", extra={"worker_name": self._name})

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                request = await self._queue.get()
            except asyncio.CancelledError:
                return
            await self._handle_one(loop, request)

    async def _handle_one(
        self,
        loop: asyncio.AbstractEventLoop,
        request: ExtractionRequest,
    ) -> None:
        """Run one job. Failures are logged and swallowed so the worker
        never dies on a poison pill — :class:`ExtractionJobService` has
        already persisted the FAILED state and the operator can retry
        via the existing route. Cancellation does propagate."""
        try:
            await loop.run_in_executor(
                None,
                self._jobs.extract,
                request.document_id,
                request.version_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - fire-and-log boundary
            # ``ExtractionJobService.extract`` already calls
            # ``mark_failed`` and emits ``extraction.failed`` with the
            # reason. We log a short worker-level breadcrumb so the
            # audit trail captures "the worker observed a failure"
            # distinct from "the parser raised."
            log.warning(
                "extraction.worker.job_failed",
                extra={
                    "worker_name": self._name,
                    "document_id": request.document_id,
                    "version_id": request.version_id,
                },
            )


__all__ = [
    "ExtractionQueue",
    "ExtractionRequest",
    "ExtractionWorker",
    "InMemoryExtractionQueue",
    "QueueFull",
]
