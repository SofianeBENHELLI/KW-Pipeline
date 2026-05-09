"""Lifespan integration tests for the extraction worker harness
(ADR-006, #40 PR-1).

Drives ``create_app`` through ``TestClient`` so the FastAPI lifespan
context manager runs end-to-end. Covers:

- ``extraction_inline=True`` (PR-1 default) → no workers spawned, app
  state's queue is ``None``, behaviour is identical to before this
  PR landed.
- ``extraction_inline=False`` → ``extraction_workers`` worker tasks
  are running while the app is up and stop cleanly on shutdown.

The route layer is **unchanged in PR-1** — these tests verify the
harness wiring, not a 202 response shape.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.dependencies import build_services
from app.main import create_app


def _services_with(
    *,
    extraction_inline: bool,
    workers: int = 1,
    queue_size: int = 4,
    recovery_interval: int = 0,
    backup_interval: int = 0,
):
    services = build_services()
    object.__setattr__(services.settings, "extraction_inline", extraction_inline)
    object.__setattr__(services.settings, "extraction_workers", workers)
    object.__setattr__(services.settings, "extraction_queue_size", queue_size)
    object.__setattr__(
        services.settings,
        "extraction_recovery_interval_seconds",
        recovery_interval,
    )
    object.__setattr__(
        services.settings,
        "backup_interval_seconds",
        backup_interval,
    )
    return services


def test_inline_default_does_not_spawn_workers() -> None:
    services = _services_with(extraction_inline=True)
    app = create_app(services=services)

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        # The lifespan attached a sentinel ``None`` queue — explicit so
        # PR-2 can branch on ``settings.extraction_inline`` and assert
        # the queue exists in async mode.
        assert app.state.extraction_queue is None
        assert app.state.extraction_workers == []


def test_async_mode_spawns_workers_and_stops_them_on_shutdown() -> None:
    services = _services_with(extraction_inline=False, workers=2, queue_size=8)
    app = create_app(services=services)

    workers = []
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert app.state.extraction_queue is not None
        assert app.state.extraction_queue.maxsize == 8
        workers = list(app.state.extraction_workers)
        assert len(workers) == 2
        assert all(worker.running for worker in workers)

    # TestClient's context manager exit triggers shutdown — every
    # worker task must have stopped cleanly.
    assert all(not worker.running for worker in workers)


def test_async_mode_skips_periodic_recovery_when_interval_is_zero() -> None:
    services = _services_with(extraction_inline=False, recovery_interval=0)
    app = create_app(services=services)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.extraction_recovery_task is None


def test_async_mode_runs_periodic_recovery_when_interval_is_positive() -> None:
    """Periodic stuck-state recovery loop is wired in async mode.

    We don't wait for a tick (the loop sleeps before the first scan, by
    design — boot recovery already covered the at-startup pass). We
    just assert the task exists, is running, and is cancelled cleanly
    on shutdown. Avoids flakiness from real-time sleeps in unit tests.
    """
    services = _services_with(extraction_inline=False, recovery_interval=1)
    app = create_app(services=services)

    task = None
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        task = app.state.extraction_recovery_task
        assert task is not None
        assert not task.done()

    # Lifespan exit cancels the task; assert it stopped without raising.
    assert task is not None
    assert task.done()
    assert task.cancelled() or task.exception() is None


def test_inline_mode_does_not_spawn_recovery_task() -> None:
    services = _services_with(extraction_inline=True, recovery_interval=900)
    app = create_app(services=services)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.extraction_recovery_task is None


def test_backup_task_skipped_when_interval_is_zero() -> None:
    services = _services_with(extraction_inline=True, backup_interval=0)
    app = create_app(services=services)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.catalog_backup_task is None


def test_backup_task_runs_when_interval_is_positive() -> None:
    """Periodic backup loop is wired regardless of extraction mode.

    The loop sleeps before its first cycle, so we don't need to wait
    for a snapshot. We just verify the task exists, is running, and
    is cancelled cleanly on shutdown.
    """
    services = _services_with(extraction_inline=True, backup_interval=1)
    app = create_app(services=services)

    task = None
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        task = app.state.catalog_backup_task
        assert task is not None
        assert not task.done()

    assert task is not None
    assert task.done()
    assert task.cancelled() or task.exception() is None


def test_lifespan_initializes_background_tasks_set() -> None:
    """``app.state.background_tasks`` must exist before any route can
    reach it. Initialized unconditionally so the validate route doesn't
    need a feature-flag check before scheduling."""
    services = _services_with(extraction_inline=True)
    app = create_app(services=services)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert isinstance(app.state.background_tasks, set)


def test_drain_background_tasks_waits_for_completion() -> None:
    """Helper drains the set within the timeout when tasks finish fast."""
    import asyncio

    from app.main import _drain_background_tasks

    async def _runner() -> None:
        async def fast() -> None:
            await asyncio.sleep(0.01)

        tasks: set[asyncio.Task[None]] = set()
        t1 = asyncio.create_task(fast())
        t2 = asyncio.create_task(fast())
        tasks.update({t1, t2})

        await _drain_background_tasks(tasks, timeout_seconds=2.0)

        assert t1.done() and not t1.cancelled()
        assert t2.done() and not t2.cancelled()

    asyncio.run(_runner())


def test_drain_background_tasks_cancels_when_timeout_exceeded() -> None:
    """A stuck task is cancelled when it doesn't finish in time."""
    import asyncio
    import contextlib

    from app.main import _drain_background_tasks

    async def _runner() -> None:
        async def stuck() -> None:
            await asyncio.sleep(60)  # Far longer than the drain timeout.

        tasks: set[asyncio.Task[None]] = set()
        t = asyncio.create_task(stuck())
        tasks.add(t)

        await _drain_background_tasks(tasks, timeout_seconds=0.1)

        # ``cancel()`` schedules the cancellation; awaiting the task
        # lets the CancelledError actually propagate so ``cancelled()``
        # flips to True.
        with contextlib.suppress(asyncio.CancelledError):
            await t
        assert t.cancelled(), "stuck task must be cancelled at timeout"

    asyncio.run(_runner())


def test_drain_background_tasks_zero_timeout_cancels_immediately() -> None:
    """``timeout_seconds=0`` skips the wait and cancels every pending task."""
    import asyncio
    import contextlib

    from app.main import _drain_background_tasks

    async def _runner() -> None:
        async def stuck() -> None:
            await asyncio.sleep(60)

        tasks: set[asyncio.Task[None]] = set()
        t = asyncio.create_task(stuck())
        tasks.add(t)

        await _drain_background_tasks(tasks, timeout_seconds=0)

        with contextlib.suppress(asyncio.CancelledError):
            await t
        assert t.cancelled()

    asyncio.run(_runner())


def test_inline_mode_existing_extract_route_is_unchanged() -> None:
    """The whole point of PR-1 being additive: the existing
    ``POST /documents/.../extract`` synchronous route still returns 200
    with the ``RawExtraction`` body when inline mode is on."""
    services = _services_with(extraction_inline=True)
    app = create_app(services=services)

    with TestClient(app) as client:
        upload = client.post(
            "/documents/upload",
            files={"file": ("note.txt", b"hello world", "text/plain")},
        )
        assert upload.status_code == 200
        version = upload.json()
        document_id = version["document_id"]
        version_id = version["id"]

        extract = client.post(f"/documents/{document_id}/versions/{version_id}/extract")
        assert extract.status_code == 200, extract.text
        assert extract.json()["parser_name"] == "plain_text"
