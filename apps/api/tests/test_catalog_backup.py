"""Unit tests for the periodic catalog backup helper."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.dependencies import build_persistent_services, build_services
from app.services.catalog_backup import (
    prune_old_snapshots,
    snapshot_catalog,
)


def _persistent_services(tmp_path: Path):
    """Build a persistent (SQLite-backed) PipelineServices rooted at tmp_path."""
    return build_persistent_services(str(tmp_path))


def _seed_one_document(services) -> None:
    """Drop one row in the catalog so the backup has content to copy."""
    services.documents.upload("seed.txt", "text/plain", b"seed content")


class TestSnapshotCatalog:
    def test_writes_snapshot_when_catalog_is_sqlite(self, tmp_path: Path) -> None:
        services = _persistent_services(tmp_path)
        _seed_one_document(services)

        dest = snapshot_catalog(services)

        assert dest is not None
        assert dest.is_file()
        assert dest.suffix == ".sqlite3"
        assert dest.parent.name == "backups"
        # Sanity: the snapshot opens and contains the seeded row.
        with sqlite3.connect(dest) as conn:
            count = conn.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0]
        assert count == 1

    def test_returns_none_for_in_memory_catalog(self) -> None:
        services = build_services()  # default = in-memory
        assert snapshot_catalog(services) is None

    def test_writes_to_explicit_backup_dir(self, tmp_path: Path) -> None:
        services = _persistent_services(tmp_path / "data")
        _seed_one_document(services)

        custom_dir = tmp_path / "off-host-backups"
        dest = snapshot_catalog(services, backup_dir=custom_dir)

        assert dest is not None
        assert dest.parent == custom_dir

    def test_filename_is_iso_timestamped(self, tmp_path: Path) -> None:
        services = _persistent_services(tmp_path)
        _seed_one_document(services)

        # Pin the timestamp so the assertion is deterministic.
        fixed = datetime(2026, 5, 9, 12, 34, 56, tzinfo=UTC)
        dest = snapshot_catalog(services, now=fixed)

        assert dest is not None
        assert dest.name == "catalog-2026-05-09T12-34-56Z.sqlite3"


class TestPruneOldSnapshots:
    def test_keeps_newest_n_files(self, tmp_path: Path) -> None:
        for ts in [
            "2026-05-01T00-00-00Z",
            "2026-05-02T00-00-00Z",
            "2026-05-03T00-00-00Z",
            "2026-05-04T00-00-00Z",
            "2026-05-05T00-00-00Z",
        ]:
            (tmp_path / f"catalog-{ts}.sqlite3").write_bytes(b"")

        deleted = prune_old_snapshots(tmp_path, retain=2)

        names_left = sorted(p.name for p in tmp_path.iterdir())
        assert names_left == [
            "catalog-2026-05-04T00-00-00Z.sqlite3",
            "catalog-2026-05-05T00-00-00Z.sqlite3",
        ]
        assert sorted(p.name for p in deleted) == [
            "catalog-2026-05-01T00-00-00Z.sqlite3",
            "catalog-2026-05-02T00-00-00Z.sqlite3",
            "catalog-2026-05-03T00-00-00Z.sqlite3",
        ]

    def test_no_op_when_count_is_at_or_below_retain(self, tmp_path: Path) -> None:
        for ts in ["2026-05-01T00-00-00Z", "2026-05-02T00-00-00Z"]:
            (tmp_path / f"catalog-{ts}.sqlite3").write_bytes(b"")
        assert prune_old_snapshots(tmp_path, retain=5) == []
        assert prune_old_snapshots(tmp_path, retain=2) == []

    def test_does_not_touch_non_matching_files(self, tmp_path: Path) -> None:
        # An operator's hand-rolled .dump or a stray README must survive.
        (tmp_path / "catalog-2026-05-01T00-00-00Z.sqlite3").write_bytes(b"")
        (tmp_path / "catalog-2026-05-02T00-00-00Z.sqlite3").write_bytes(b"")
        (tmp_path / "manual-dump.sql").write_text("-- by operator")
        (tmp_path / "README.md").write_text("backup conventions")

        prune_old_snapshots(tmp_path, retain=1)

        names_left = sorted(p.name for p in tmp_path.iterdir())
        assert "manual-dump.sql" in names_left
        assert "README.md" in names_left

    def test_rejects_non_positive_retain(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            prune_old_snapshots(tmp_path, retain=0)

    def test_handles_missing_directory(self, tmp_path: Path) -> None:
        assert prune_old_snapshots(tmp_path / "nope", retain=3) == []
