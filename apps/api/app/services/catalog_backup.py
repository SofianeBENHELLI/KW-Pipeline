"""Periodic snapshot of the SQLite catalog.

Uptime in the user-perceptible sense includes "did I lose my work."
A container that ran for three weeks is not actually meeting its goal
if a corrupted catalog wipes a month of validations. This module ships
a small background backup job that runs alongside the extraction
worker lifespan.

Design choices:

- **Online backup, not file copy.** Uses :meth:`sqlite3.Connection.backup`,
  which is the same algorithm as the ``sqlite3 .backup`` shell command —
  it acquires no writer lock, tolerates concurrent writes, and produces
  a transactionally consistent file. ``cp`` of a live SQLite DB can
  yield a torn file if a write lands mid-copy.
- **Best-effort.** A failed snapshot is logged and the loop continues.
  Losing one cycle is far better than killing the loop.
- **No-op when not SQLite-backed.** The default in-memory wiring has
  no file to back up; ``snapshot_catalog`` returns ``None`` and the
  periodic loop logs a one-time skip and exits.
- **Bounded retention.** The newest ``retain`` files survive; older
  ones are pruned each cycle so a long-running container doesn't fill
  the volume.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.catalog_store import SQLiteCatalogStore

if TYPE_CHECKING:
    from app.dependencies import PipelineServices

log = logging.getLogger(__name__)

_BACKUP_DIR_NAME = "backups"
_BACKUP_FILENAME_PREFIX = "catalog-"
_BACKUP_FILENAME_SUFFIX = ".sqlite3"


def _resolve_source_path(services: PipelineServices) -> Path | None:
    """Return the live SQLite catalog path, or ``None`` for in-memory."""
    catalog = services.documents.catalog
    if not isinstance(catalog, SQLiteCatalogStore):
        return None
    return catalog.database_path


def _format_timestamp(now: datetime) -> str:
    """``2026-05-09T11-42-03Z`` — filesystem-safe, sortable, UTC."""
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")


def snapshot_catalog(
    services: PipelineServices,
    *,
    backup_dir: Path | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Write one online backup of the catalog to ``backup_dir``.

    Returns the path of the new snapshot, or ``None`` when the catalog
    is not SQLite-backed (in-memory wiring; nothing to back up).
    """
    source_path = _resolve_source_path(services)
    if source_path is None:
        return None

    if backup_dir is None:
        backup_dir = source_path.parent / _BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _format_timestamp(now or datetime.now(UTC))
    dest = backup_dir / f"{_BACKUP_FILENAME_PREFIX}{timestamp}{_BACKUP_FILENAME_SUFFIX}"

    # Read-only on the source so a writer in another connection can
    # keep going. ``Connection.backup`` is the canonical hot-backup
    # path — it iterates pages with internal locking.
    src_conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    return dest


def prune_old_snapshots(
    backup_dir: Path,
    *,
    retain: int,
) -> list[Path]:
    """Delete every snapshot beyond the newest ``retain`` files.

    Returns the deleted paths (in deletion order). The pattern guard
    means stray files in ``backup_dir`` (e.g. an operator's manual
    ``.dump``) are never touched.
    """
    if retain < 1:
        raise ValueError("retain must be >= 1")
    if not backup_dir.is_dir():
        return []

    pattern = f"{_BACKUP_FILENAME_PREFIX}*{_BACKUP_FILENAME_SUFFIX}"
    snapshots = sorted(
        (p for p in backup_dir.glob(pattern) if p.is_file()),
        key=lambda p: p.name,  # ISO-like timestamp → lexicographic == chronological.
    )
    if len(snapshots) <= retain:
        return []

    to_delete = snapshots[:-retain]
    for path in to_delete:
        try:
            path.unlink()
        except OSError as exc:  # pragma: no cover - filesystem race
            log.warning(
                "catalog_backup.prune_failed",
                extra={"path": str(path), "error_type": type(exc).__name__},
            )
    return to_delete
