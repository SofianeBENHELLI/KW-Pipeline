"""Coverage for ``StorageService.delete()`` (ADR-027 §7, slice 3).

The deletion primitive is the foundation for the deferred
``purge_artifacts`` slice — it lands now so the contract is settled
across both store impls. Per ADR-027 §7:

- **Best-effort + idempotent.** Deleting a missing object is not an
  error. The motivating case is a partial prior purge that left the
  catalog out of sync with the storage backend; the retry must
  converge, not fail.
- **Single-object, no hierarchical fan-out.** ``delete(uri)`` removes
  exactly one object; the orchestration of N URIs per document lives
  in the (deferred) admin-tool slice 4.

The test suite parametrizes over both ``InMemoryStorageService`` and
``FileSystemStorageService`` so the contract is locked at the
Protocol boundary, not at one impl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.storage_service import (
    FileSystemStorageService,
    InMemoryStorageService,
    StorageService,
)


@pytest.fixture(params=["memory", "filesystem"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> StorageService:
    """Build a storage backend keyed off the parametrize id."""
    if request.param == "memory":
        return InMemoryStorageService()
    return FileSystemStorageService(tmp_path)


class TestDeleteExistingObject:
    """Happy path: an object that was just put can be deleted."""

    def test_delete_existing_object_round_trips(self, storage: StorageService) -> None:
        uri = storage.put("documents/v1/policy.txt", b"content")
        assert storage.get(uri) == b"content"

        storage.delete(uri)

        # After deletion the object is gone — the read path raises (the
        # exception type is per-impl: ``KeyError`` for in-memory,
        # ``FileNotFoundError`` for filesystem). The contract this slice
        # locks is "delete dropped the object"; the read failure shape
        # is already covered by the existing storage tests.
        with pytest.raises((KeyError, FileNotFoundError)):
            storage.get(uri)

    def test_delete_one_object_does_not_affect_others(
        self,
        storage: StorageService,
    ) -> None:
        first = storage.put("documents/v1/a.txt", b"alpha")
        second = storage.put("documents/v2/b.txt", b"beta")

        storage.delete(first)

        # Sibling object survives — single-object semantics per ADR-027 §7.
        assert storage.get(second) == b"beta"


class TestDeleteMissingObject:
    """Idempotency path: deleting a URI that doesn't exist is a no-op."""

    def test_delete_missing_object_is_silent(self, storage: StorageService) -> None:
        # The contract: deleting a URI that was never put is not an
        # error. Pick a URI shape the impl would have produced so we
        # exercise the impl's own "is this mine?" guard.
        if isinstance(storage, InMemoryStorageService):
            missing = "memory://documents/never-put/policy.txt"
        else:
            assert isinstance(storage, FileSystemStorageService)
            missing = (storage.root / "documents" / "ghost" / "policy.txt").as_uri()

        storage.delete(missing)  # MUST NOT raise

    def test_delete_after_delete_is_silent(self, storage: StorageService) -> None:
        """Convergent retry: a second delete on the same URI is a no-op.

        Pins the partial-prior-purge motivating case: if the catalog
        thinks a URI still exists but the bytes were already removed
        out of band, the retry MUST converge on the missing state
        rather than failing.
        """
        uri = storage.put("documents/v1/twice.txt", b"content")

        storage.delete(uri)
        storage.delete(uri)  # MUST NOT raise


class TestFilesystemDeletePathSafety:
    """Filesystem-only: the URI safety guard from ``get`` must apply.

    ``FileSystemStorageService.delete`` validates the URI is rooted
    beneath the configured ``root`` — same guard ``get`` uses — so a
    stray path-traversal attempt fails fast instead of silently
    unlinking an unrelated file. This is the only behaviour that
    differs between impls (``InMemoryStorageService`` has no
    filesystem to guard).
    """

    def test_rejects_non_file_uri(self, tmp_path: Path) -> None:
        storage = FileSystemStorageService(tmp_path)

        with pytest.raises(ValueError, match="file://"):
            storage.delete("memory://something")

    def test_rejects_uri_outside_root(self, tmp_path: Path) -> None:
        storage = FileSystemStorageService(tmp_path)

        # A URI rooted outside ``tmp_path`` must not be unlinkable
        # through this storage instance — that's the path-traversal
        # guard from ``get`` carried into ``delete``.
        outside = (tmp_path.parent / "elsewhere.txt").as_uri()
        with pytest.raises(ValueError, match="outside"):
            storage.delete(outside)
