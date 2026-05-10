"""Tests for the Neo4j backup helper (#381 / ADR-031).

The backup mechanism shells out via :mod:`subprocess`; tests stub
the runner so no real ``neo4j-admin`` is needed. Coverage:

* Settings defaults — disabled by default, all knobs constrained.
* ``snapshot_neo4j`` returns ``None`` when the dest dir is empty
  (defensive — the boot guard catches this in production).
* ``snapshot_neo4j`` writes to a timestamped subdir + invokes the
  runner with the rendered command.
* Template renders all three placeholders.
* Custom commands (Enterprise / sidecar / webhook) round-trip.
* Runner failures propagate so the lifespan loop can audit.
* Retention prunes oldest dump subdirectories, leaves stranger
  layouts (manual dumps) alone.
* Cycle helper emits ``ops.neo4j_backup.completed`` /
  ``ops.neo4j_backup.failed`` audit events as documented.
* Lifespan boot guard fires when interval > 0 but dir is empty.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import _run_one_neo4j_backup_cycle, create_app
from app.services.neo4j_backup import (
    prune_old_neo4j_snapshots,
    snapshot_neo4j,
)
from app.settings import Settings

# ─── Settings defaults ───────────────────────────────────────────


def test_neo4j_backup_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KW_NEO4J_BACKUP_INTERVAL_SECONDS", raising=False)
    s = Settings()
    assert s.neo4j_backup_interval_seconds == 0
    assert s.neo4j_backup_retain_count == 7
    assert s.neo4j_backup_dir == ""


def test_neo4j_backup_settings_can_be_tuned_via_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KW_NEO4J_BACKUP_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("KW_NEO4J_BACKUP_RETAIN_COUNT", "14")
    monkeypatch.setenv("KW_NEO4J_BACKUP_DIR", str(tmp_path))
    s = Settings()
    assert s.neo4j_backup_interval_seconds == 3600
    assert s.neo4j_backup_retain_count == 14
    assert s.neo4j_backup_dir == str(tmp_path)


def test_neo4j_backup_retain_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_NEO4J_BACKUP_RETAIN_COUNT", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_neo4j_backup_interval_must_be_non_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KW_NEO4J_BACKUP_INTERVAL_SECONDS", "-1")
    with pytest.raises(ValidationError):
        Settings()


# ─── snapshot_neo4j ───────────────────────────────────────────────


def _settings(
    *,
    backup_dir: str = "",
    command: str | None = None,
    database: str = "neo4j",
) -> Settings:
    """Build a Settings model with explicit field values, bypassing env."""
    kwargs: dict = {
        "neo4j_backup_dir": backup_dir,
        "neo4j_database": database,
    }
    if command is not None:
        kwargs["neo4j_backup_command"] = command
    return Settings(**kwargs)


def test_snapshot_returns_none_when_no_dest_configured() -> None:
    """Defensive — production wiring's boot guard prevents this, but
    the helper must stay safe to call from anywhere."""
    settings = _settings(backup_dir="")
    runner_calls: list[tuple] = []

    def _runner(*, command: list[str], cwd: Path) -> None:
        runner_calls.append((command, cwd))

    assert snapshot_neo4j(settings, runner=_runner) is None
    assert runner_calls == []


def test_snapshot_creates_timestamped_subdir_and_invokes_runner(tmp_path: Path) -> None:
    settings = _settings(backup_dir=str(tmp_path))
    captured: dict = {}

    def _runner(*, command: list[str], cwd: Path) -> None:
        captured["command"] = command
        captured["cwd"] = cwd

    when = datetime(2026, 5, 10, 14, 30, 0, tzinfo=UTC)
    dest = snapshot_neo4j(settings, now=when, runner=_runner)
    assert dest is not None
    assert dest.name == "2026-05-10T14-30-00Z"
    assert dest.is_dir()
    assert captured["cwd"] == dest
    # Default command renders with the database name + dest path.
    assert captured["command"] == [
        "neo4j-admin",
        "database",
        "dump",
        "neo4j",
        f"--to-path={dest}",
    ]


def test_snapshot_template_renders_all_placeholders(tmp_path: Path) -> None:
    """Operators with custom backup tooling can override the command
    template and reference any of the three placeholders."""
    settings = _settings(
        backup_dir=str(tmp_path),
        command="my-tool --db {database} --out {dest_dir} --tag {timestamp}",
        database="custom_db",
    )
    captured: list[list[str]] = []
    snapshot_neo4j(
        settings,
        now=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        runner=lambda *, command, cwd: captured.append(command),  # noqa: ARG005
    )
    assert captured[0] == [
        "my-tool",
        "--db",
        "custom_db",
        "--out",
        str(tmp_path / "2026-01-02T03-04-05Z"),
        "--tag",
        "2026-01-02T03-04-05Z",
    ]


def test_snapshot_with_quoted_path_in_command_uses_shlex_split(tmp_path: Path) -> None:
    """Quoting in the command template is POSIX-shell-style."""
    weird_dir = tmp_path / "with space"
    settings = _settings(
        backup_dir=str(weird_dir),
        command='custom "{dest_dir}/dump.tar.gz"',
    )
    captured: list[list[str]] = []
    snapshot_neo4j(
        settings,
        now=datetime(2026, 5, 1, tzinfo=UTC),
        runner=lambda *, command, cwd: captured.append(command),  # noqa: ARG005
    )
    # Single quoted argument joined into one argv element.
    assert captured[0] == [
        "custom",
        f"{weird_dir}/2026-05-01T00-00-00Z/dump.tar.gz",
    ]


def test_snapshot_empty_command_template_raises(tmp_path: Path) -> None:
    settings = _settings(backup_dir=str(tmp_path), command="   ")
    with pytest.raises(ValueError, match="empty argv"):
        snapshot_neo4j(settings, runner=lambda *, command, cwd: None)  # noqa: ARG005


def test_snapshot_propagates_runner_failure(tmp_path: Path) -> None:
    """A failed dump raises out of the helper so the lifespan loop
    can audit + decide to keep looping."""
    settings = _settings(backup_dir=str(tmp_path))

    def _boom(*, command: list[str], cwd: Path) -> None:
        raise subprocess.CalledProcessError(returncode=1, cmd=command, stderr="boom")

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        snapshot_neo4j(settings, runner=_boom)
    assert excinfo.value.returncode == 1


# ─── prune_old_neo4j_snapshots ───────────────────────────────────


def test_prune_keeps_newest_n_dump_subdirs(tmp_path: Path) -> None:
    for stamp in (
        "2026-05-01T00-00-00Z",
        "2026-05-02T00-00-00Z",
        "2026-05-03T00-00-00Z",
        "2026-05-04T00-00-00Z",
    ):
        (tmp_path / stamp).mkdir()

    pruned = prune_old_neo4j_snapshots(tmp_path, retain=2)
    pruned_names = sorted(p.name for p in pruned)
    assert pruned_names == ["2026-05-01T00-00-00Z", "2026-05-02T00-00-00Z"]
    survivors = sorted(p.name for p in tmp_path.iterdir())
    assert survivors == ["2026-05-03T00-00-00Z", "2026-05-04T00-00-00Z"]


def test_prune_leaves_non_timestamp_subdirs_alone(tmp_path: Path) -> None:
    """Operators sometimes park manual dumps alongside the auto ones —
    the pruner only touches subdirectories matching the timestamp
    pattern."""
    (tmp_path / "2026-05-01T00-00-00Z").mkdir()
    (tmp_path / "2026-05-02T00-00-00Z").mkdir()
    (tmp_path / "2026-05-03T00-00-00Z").mkdir()
    (tmp_path / "manual-may-fix").mkdir()

    prune_old_neo4j_snapshots(tmp_path, retain=1)
    survivors = sorted(p.name for p in tmp_path.iterdir())
    # Only the newest auto-dump + the manual subdir survive.
    assert "2026-05-03T00-00-00Z" in survivors
    assert "manual-may-fix" in survivors
    assert "2026-05-01T00-00-00Z" not in survivors


def test_prune_no_op_when_under_retain(tmp_path: Path) -> None:
    (tmp_path / "2026-05-01T00-00-00Z").mkdir()
    pruned = prune_old_neo4j_snapshots(tmp_path, retain=7)
    assert pruned == []
    assert (tmp_path / "2026-05-01T00-00-00Z").exists()


def test_prune_rejects_zero_retain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retain must be >= 1"):
        prune_old_neo4j_snapshots(tmp_path, retain=0)


def test_prune_no_op_on_missing_dir(tmp_path: Path) -> None:
    assert prune_old_neo4j_snapshots(tmp_path / "nope", retain=3) == []


# ─── _run_one_neo4j_backup_cycle (audit events) ──────────────────


def test_cycle_emits_completed_event_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("KW_NEO4J_BACKUP_DIR", str(tmp_path))
    # Replace the actual snapshot to avoid shelling out.
    captured_dest: list[Path] = []

    def _fake_snapshot(settings, *, now=None, runner=None):  # noqa: ARG001
        del settings, now, runner
        dest = tmp_path / "2026-05-10T00-00-00Z"
        dest.mkdir()
        captured_dest.append(dest)
        return dest

    monkeypatch.setattr("app.main.snapshot_neo4j", _fake_snapshot)

    settings = Settings(neo4j_backup_dir=str(tmp_path))
    with caplog.at_level(logging.INFO, logger="app.main"):
        outcome = _run_one_neo4j_backup_cycle(settings, retain=3)
    assert outcome == "ok"
    assert any(rec.message == "ops.neo4j_backup.completed" for rec in caplog.records)


def test_cycle_emits_failed_event_on_called_process_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_snapshot(*args, **kwargs):  # noqa: ARG001
        raise subprocess.CalledProcessError(
            returncode=2,
            cmd=["neo4j-admin", "database", "dump"],
            stderr="permission denied on data dir",
        )

    monkeypatch.setattr("app.main.snapshot_neo4j", _fake_snapshot)

    settings = Settings(neo4j_backup_dir=str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="app.main"):
        outcome = _run_one_neo4j_backup_cycle(settings, retain=3)
    assert outcome == "error"
    failed = [rec for rec in caplog.records if rec.message == "ops.neo4j_backup.failed"]
    assert len(failed) == 1
    rec = failed[0]
    assert getattr(rec, "returncode", None) == 2
    assert "permission denied" in getattr(rec, "stderr", "")


def test_cycle_emits_failed_event_on_unexpected_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_snapshot(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("simulated outage")

    monkeypatch.setattr("app.main.snapshot_neo4j", _fake_snapshot)

    settings = Settings(neo4j_backup_dir=str(tmp_path))
    with caplog.at_level(logging.WARNING, logger="app.main"):
        outcome = _run_one_neo4j_backup_cycle(settings, retain=3)
    assert outcome == "error"
    assert any(rec.message == "ops.neo4j_backup.failed" for rec in caplog.records)


def test_cycle_returns_error_when_dest_unconfigured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _fake_snapshot(*args, **kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr("app.main.snapshot_neo4j", _fake_snapshot)
    settings = Settings(neo4j_backup_dir="")
    with caplog.at_level(logging.WARNING, logger="app.main"):
        outcome = _run_one_neo4j_backup_cycle(settings, retain=3)
    assert outcome == "error"
    assert any(rec.message == "ops.neo4j_backup.skipped" for rec in caplog.records)


# ─── Lifespan boot guard ─────────────────────────────────────────


def test_lifespan_boot_fails_when_interval_set_without_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KW_NEO4J_BACKUP_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("KW_NEO4J_BACKUP_DIR", "")
    app = create_app()
    with pytest.raises(RuntimeError, match="KW_NEO4J_BACKUP_DIR is empty"), TestClient(app):
        pass


def test_lifespan_default_does_not_spawn_neo4j_backup_task() -> None:
    """Disabled by default → ``app.state.neo4j_backup_task`` is None
    after lifespan startup."""
    app = create_app()
    with TestClient(app):
        assert app.state.neo4j_backup_task is None


def test_lifespan_spawns_neo4j_backup_task_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KW_NEO4J_BACKUP_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("KW_NEO4J_BACKUP_DIR", str(tmp_path))
    # Stub the cycle so the task does no real work during the test.
    monkeypatch.setattr("app.main._run_one_neo4j_backup_cycle", lambda *_a, **_kw: "ok")

    app = create_app()
    with TestClient(app):
        task = app.state.neo4j_backup_task
        assert task is not None
        assert task.get_name() == "neo4j-backup"
