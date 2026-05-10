"""Periodic Neo4j knowledge-graph backup (#381 / ADR-031).

The SQLite catalog backup ships in :mod:`app.services.catalog_backup`
and uses SQLite's online backup API in-process. Neo4j has no
equivalent in-process API — production backups go through
``neo4j-admin database dump`` (Community) or the online backup
endpoint (Enterprise), both of which run **outside** the API
container in typical deployments.

This module ships the **scheduling + retention + audit scaffold**
and shells out to an operator-supplied command via
:mod:`subprocess`. Defaults assume Community + co-located
``neo4j-admin``; operators on Enterprise / Kubernetes / sidecar
backup orchestrators override ``KW_NEO4J_BACKUP_COMMAND`` to fit.

Design choices:

- **Disabled by default.** Operator opts in by setting
  ``KW_NEO4J_BACKUP_INTERVAL_SECONDS > 0``. Default behaviour is
  unchanged.
- **Best-effort.** A failed dump is logged and the loop continues.
  Same posture as the SQLite catalog backup — losing one cycle is
  far better than killing the loop.
- **No graph-store dependency.** The dump command runs against the
  configured Neo4j database directly (via ``neo4j-admin`` or
  whatever the operator wires); we don't touch
  :class:`Neo4jGraphStore`. That keeps the helper functional even
  when the API can't reach Neo4j over Bolt (the very situation
  where you'd want a backup).
- **Bounded retention.** The newest ``retain`` timestamped
  subdirectories survive; older ones are pruned each cycle.
"""

from __future__ import annotations

import contextlib
import logging
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.settings import Settings

log = logging.getLogger(__name__)


_BACKUP_SUBDIR_FORMAT = "%Y-%m-%dT%H-%M-%SZ"


class BackupRunner(Protocol):
    """Boundary the test suite stubs to avoid invoking real ``neo4j-admin``.

    The default implementation runs the configured command via
    :func:`subprocess.run` and raises
    :class:`subprocess.CalledProcessError` on non-zero exit.
    """

    def __call__(self, *, command: list[str], cwd: Path) -> None: ...


def _default_runner(*, command: list[str], cwd: Path) -> None:
    """Real ``subprocess.run`` runner. Raises on non-zero exit."""
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def snapshot_neo4j(
    settings: Settings,
    *,
    now: datetime | None = None,
    runner: BackupRunner | None = None,
) -> Path | None:
    """Run one Neo4j dump cycle. Pure-sync helper.

    Returns the timestamped destination directory the dump landed in,
    or ``None`` when no destination is configured (operator has set
    the interval > 0 but left ``KW_NEO4J_BACKUP_DIR`` empty — the
    boot guard catches that, but defensive ``None`` here keeps the
    helper safe to call from anywhere).

    Raises :class:`subprocess.CalledProcessError` when the runner
    fails. The lifespan loop catches and logs.
    """
    backup_root = (settings.neo4j_backup_dir or "").strip()
    if not backup_root:
        return None

    when = now or datetime.now(UTC)
    timestamp = when.strftime(_BACKUP_SUBDIR_FORMAT)
    dest_dir = Path(backup_root) / timestamp
    dest_dir.mkdir(parents=True, exist_ok=True)

    template = settings.neo4j_backup_command.strip()
    rendered = template.format(
        database=settings.neo4j_database or "neo4j",
        dest_dir=str(dest_dir),
        timestamp=timestamp,
    )
    command = shlex.split(rendered)
    if not command:
        # Misconfigured template — skip without leaving a partial
        # subdirectory behind. Cleanup is best-effort; a failure here
        # (directory not empty / race) doesn't change the outcome.
        with contextlib.suppress(OSError):
            dest_dir.rmdir()
        raise ValueError("KW_NEO4J_BACKUP_COMMAND rendered to an empty argv after shlex.split")

    runner = runner or _default_runner
    runner(command=command, cwd=dest_dir)
    return dest_dir


def prune_old_neo4j_snapshots(
    backup_dir: Path,
    *,
    retain: int,
) -> list[Path]:
    """Delete every dump subdirectory beyond the newest ``retain``.

    Returns the deleted paths (in deletion order). Subdirectories
    not matching the timestamp pattern are ignored — operators can
    park their own ``manual-{date}/`` dumps in the same folder
    without losing them.
    """
    if retain < 1:
        raise ValueError("retain must be >= 1")
    if not backup_dir.is_dir():
        return []

    snapshots = sorted(
        (entry for entry in backup_dir.iterdir() if _looks_like_dump(entry)),
        key=lambda p: p.name,
    )
    if len(snapshots) <= retain:
        return []

    to_delete = snapshots[:-retain]
    for path in to_delete:
        try:
            shutil.rmtree(path)
        except OSError as exc:  # pragma: no cover - filesystem race
            log.warning(
                "neo4j_backup.prune_failed",
                extra={"path": str(path), "error_type": type(exc).__name__},
            )
    return to_delete


def _looks_like_dump(entry: Path) -> bool:
    """Permissive timestamp-subdirectory check.

    The format we write is ``YYYY-MM-DDTHH-MM-SSZ``. We don't full-
    parse it (cheap is fine — a missing dump just means nothing to
    prune), we just require directory + the right shape: a name that
    starts with 4 digits and contains the trailing ``Z``.
    """
    if not entry.is_dir():
        return False
    name = entry.name
    return len(name) >= 5 and name[:4].isdigit() and name.endswith("Z")


__all__ = [
    "BackupRunner",
    "prune_old_neo4j_snapshots",
    "snapshot_neo4j",
]
