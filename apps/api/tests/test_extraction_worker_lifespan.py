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


def test_run_one_backup_cycle_returns_ok_for_sqlite_catalog(tmp_path) -> None:
    """The cycle helper writes a snapshot when the catalog is on disk."""
    from app.dependencies import build_persistent_services
    from app.main import _run_one_backup_cycle

    services = build_persistent_services(str(tmp_path))
    services.documents.upload("seed.txt", "text/plain", b"seed body")

    outcome = _run_one_backup_cycle(services, retain=3)

    assert outcome == "ok"
    backups = list((tmp_path / "backups").glob("catalog-*.sqlite3"))
    assert len(backups) == 1


def test_run_one_backup_cycle_returns_in_memory_when_no_file() -> None:
    """The cycle helper signals the loop to stop when no SQLite file exists."""
    from app.main import _run_one_backup_cycle

    services = build_services()  # default = in-memory
    assert _run_one_backup_cycle(services, retain=3) == "in_memory"


def test_run_one_backup_cycle_returns_error_when_snapshot_raises(monkeypatch, tmp_path) -> None:
    """A failed snapshot is logged and returns ``error`` so the loop continues."""
    from app.main import _run_one_backup_cycle

    services = build_services()

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("app.main.snapshot_catalog", boom)
    assert _run_one_backup_cycle(services, retain=3) == "error"


def test_run_one_backup_cycle_continues_when_prune_fails(monkeypatch, tmp_path) -> None:
    """Prune failure is non-fatal; the snapshot still counts as ``ok``."""
    from pathlib import Path

    from app.main import _run_one_backup_cycle

    services = build_services()

    def fake_snapshot(_services, **_kwargs) -> Path:
        # Pretend we wrote a file at this path; the prune step will fail.
        return tmp_path / "backups" / "catalog-2026-05-09T00-00-00Z.sqlite3"

    def fake_prune(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("app.main.snapshot_catalog", fake_snapshot)
    monkeypatch.setattr("app.main.prune_old_snapshots", fake_prune)

    assert _run_one_backup_cycle(services, retain=3) == "ok"


def test_run_one_backup_cycle_logs_pruned_count(monkeypatch, tmp_path, caplog) -> None:
    """The cycle emits a structured log line listing the pruned count."""
    import logging
    from pathlib import Path

    from app.main import _run_one_backup_cycle

    services = build_services()

    def fake_snapshot(_services, **_kwargs) -> Path:
        return tmp_path / "backups" / "catalog-2026-05-09T00-00-00Z.sqlite3"

    def fake_prune(*_args, **_kwargs):
        return [tmp_path / "old1.sqlite3", tmp_path / "old2.sqlite3"]

    monkeypatch.setattr("app.main.snapshot_catalog", fake_snapshot)
    monkeypatch.setattr("app.main.prune_old_snapshots", fake_prune)

    with caplog.at_level(logging.INFO, logger="app.main"):
        _run_one_backup_cycle(services, retain=3)
    assert any(
        rec.message == "catalog_backup.pruned" and getattr(rec, "pruned_count", None) == 2
        for rec in caplog.records
    )


def test_run_one_stuck_extraction_recovery_swallows_errors(monkeypatch) -> None:
    """The recovery helper logs and swallows so the loop can't die."""
    from app.main import _run_one_stuck_extraction_recovery

    services = build_services()

    def boom(*_args, **_kwargs):
        raise RuntimeError("catalog unreachable")

    monkeypatch.setattr("app.main.recover_stuck_extractions", boom)
    # Returns None and does NOT raise.
    assert _run_one_stuck_extraction_recovery(services) is None


def test_run_one_stuck_extraction_recovery_logs_when_recovered(monkeypatch, caplog) -> None:
    """A non-zero recover count emits a structured log."""
    import logging

    from app.main import _run_one_stuck_extraction_recovery

    services = build_services()
    monkeypatch.setattr("app.main.recover_stuck_extractions", lambda _s: 3)

    with caplog.at_level(logging.INFO, logger="app.main"):
        _run_one_stuck_extraction_recovery(services)

    assert any(
        rec.message == "extraction.recovery.periodic_scan_recovered"
        and getattr(rec, "recovered_count", None) == 3
        for rec in caplog.records
    )


def test_periodic_catalog_backup_loop_exits_on_in_memory_outcome() -> None:
    """The loop runs one iteration and exits when the cycle returns
    ``in_memory`` — proves the body of the loop (not just the sleep) is
    executed.

    Uses :func:`asyncio.wait_for` with a generous 2-second wall-clock
    timeout rather than spin-counting event-loop ticks (#384). The
    loop body uses ``asyncio.to_thread`` which depends on the OS
    thread scheduler — on a busy CI runner the worker can take more
    than the previous "20 ticks" budget to hand back, producing
    spurious "loop did not exit" failures on py3.12. A real timeout
    surfaces a genuine hang as a clean ``TimeoutError`` while
    tolerating thread-pool latency variance.
    """
    import asyncio

    from app.main import _periodic_catalog_backup

    async def _runner() -> None:
        services = build_services()  # in-memory → cycle returns "in_memory"
        task = asyncio.create_task(
            _periodic_catalog_backup(services, interval_seconds=0, retain=3),
        )
        # 2s is enormous in event-loop time but bounded enough to
        # surface a real hang; ``wait_for`` re-raises any task
        # exception so the assertion below is just a defensive
        # ``done`` check.
        await asyncio.wait_for(task, timeout=2.0)
        assert task.done()

    asyncio.run(_runner())


def test_periodic_stuck_extraction_recovery_loop_runs_one_cycle(monkeypatch) -> None:
    """The recovery loop runs the body and continues until cancelled.

    We monkeypatch the helper to record calls so we can prove the loop
    body actually fired before we cancel.
    """
    import asyncio
    import contextlib

    from app.main import _periodic_stuck_extraction_recovery

    calls: list[str] = []

    def _record(_services) -> None:
        calls.append("call")

    monkeypatch.setattr("app.main._run_one_stuck_extraction_recovery", _record)

    async def _runner() -> None:
        services = build_services()
        task = asyncio.create_task(
            _periodic_stuck_extraction_recovery(services, interval_seconds=0),
        )
        # Wait for at least one cycle to fire.
        for _ in range(50):
            await asyncio.sleep(0)
            if calls:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert calls, "loop body never executed before cancellation"

    asyncio.run(_runner())


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
