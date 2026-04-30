from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

# Maximum sanitized basename length, in code points. Matches common filesystem
# component limits (255) with headroom for the documents/<uuid>/ prefix.
_MAX_KEY_BASENAME_LENGTH = 200

# Streaming write granularity. 8 MiB is large enough that syscall overhead is
# amortised on real disks, small enough that peak resident memory stays
# bounded for multi-gigabyte uploads.
_STREAM_CHUNK_SIZE = 8 * 1024 * 1024


def safe_storage_key(version_id: str, filename: str) -> str:
    """Build a storage key from a version ID and a sanitized filename.

    Strips path components (only the trailing basename is used), replaces
    every character outside `[A-Za-z0-9._-]` and unicode alphanumerics with
    `_`, removes leading dots so dotfiles can't be created, caps the
    sanitized name at 200 code points, and falls back to ``"upload"``
    when the result would be empty. Only the storage key is sanitized —
    the user-facing ``DocumentVersion.filename`` is preserved as-is.
    """
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base)
    safe = safe.lstrip(".")
    safe = safe[:_MAX_KEY_BASENAME_LENGTH]
    if not safe:
        safe = "upload"
    return f"documents/{version_id}/{safe}"


class StorageService(Protocol):
    """Object storage boundary for raw uploaded bytes."""

    def put(self, key: str, content: bytes) -> str:
        """Store bytes and return a URI-like handle."""

    def put_stream(self, key: str, chunks: Iterable[bytes]) -> str:
        """Store an iterable of byte chunks incrementally and return a URI."""

    def get(self, uri: str) -> bytes:
        """Load bytes from a URI-like handle."""


@dataclass
class InMemoryStorageService:
    """Small object-store adapter for local tests and MVP demos."""

    objects: dict[str, bytes] = field(default_factory=dict)

    def put(self, key: str, content: bytes) -> str:
        """Store bytes and return a URI-like handle."""
        uri = f"memory://{key}"
        self.objects[uri] = content
        return uri

    def put_stream(self, key: str, chunks: Iterable[bytes]) -> str:
        """Accumulate an iterable of chunks and store the joined bytes.

        The in-memory backend has nowhere to spool to, so we accumulate into
        a ``bytearray`` and store the result. Real object stores would
        forward each chunk to a multipart upload — the API matches.
        """
        buffer = bytearray()
        for chunk in chunks:
            buffer.extend(chunk)
        return self.put(key, bytes(buffer))

    def get(self, uri: str) -> bytes:
        """Load bytes previously stored under a memory URI."""
        return self.objects[uri]


@dataclass
class FileSystemStorageService:
    """Filesystem object-store adapter for the local persistent MVP."""

    root: Path | str

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes) -> str:
        """Store bytes below the configured root and return a `file://` URI."""
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path.resolve().as_uri()

    def put_stream(self, key: str, chunks: Iterable[bytes]) -> str:
        """Stream chunks to disk without materialising the full payload.

        Opens the destination once and writes each chunk as it arrives; the
        OS page cache plus the caller's chunk size (typically 8 MiB) bound
        the resident set. Returns the same ``file://`` URI as ``put``.
        """
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            for chunk in chunks:
                fh.write(chunk)
        return path.resolve().as_uri()

    def get(self, uri: str) -> bytes:
        """Load bytes from a `file://` URI under the configured root."""
        path = self._path_from_uri(uri)
        return path.read_bytes()

    def _path_for_key(self, key: str) -> Path:
        key_path = PurePosixPath(key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise ValueError("Storage key must be a relative path without parent traversal.")
        return self.root.joinpath(*key_path.parts)

    def _path_from_uri(self, uri: str) -> Path:
        if not uri.startswith("file://"):
            raise ValueError("FileSystemStorageService only supports file:// URIs.")
        path = Path(uri.removeprefix("file://")).resolve()
        root = self.root.resolve()
        if path != root and root not in path.parents:
            raise ValueError("Storage URI points outside the configured root.")
        return path
