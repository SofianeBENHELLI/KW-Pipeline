import base64
import binascii
import copy
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.models.document import (
    ALLOWED_PREDECESSORS,
    DocumentVersionStatus,
    IllegalTransition,
)
from app.schemas.document import Document, DocumentVersion
from app.schemas.extraction import RawExtraction
from app.schemas.semantic_document import SemanticDocument
from app.services.semantic_schema_loader import load_semantic_document

ReviewedStatus = DocumentVersionStatus  # narrowed to VALIDATED | REJECTED at the call site


class InvalidCursor(ValueError):
    """Raised when a pagination cursor cannot be decoded.

    The route layer maps this to HTTP 400 with the message in ``detail`` so
    clients can debug malformed cursors instead of seeing a 500.
    """


def _encode_cursor(position: tuple[datetime, str]) -> str:
    """Encode a ``(created_at, id)`` pair as an opaque URL-safe base64 token.

    The wire format is JSON inside base64 so the codec stays readable in
    server logs while remaining opaque to clients. Callers MUST treat the
    returned string as opaque — its shape is not part of the public API.
    """
    created_at, document_id = position
    payload = json.dumps([created_at.isoformat(), document_id]).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(token: str) -> tuple[datetime, str]:
    """Decode an opaque cursor back into a ``(created_at, id)`` tuple.

    Raises :class:`InvalidCursor` for malformed base64, malformed JSON,
    wrong shape (not a 2-element list), or wrong types. The error message
    is safe to surface to clients — it never leaks server state.
    """
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise InvalidCursor(f"Cursor is not valid base64: {exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursor(f"Cursor payload is not valid JSON: {exc}") from exc
    if not isinstance(decoded, list) or len(decoded) != 2:
        raise InvalidCursor("Cursor payload must be a [created_at, id] pair.")
    created_at_raw, document_id = decoded
    if not isinstance(created_at_raw, str) or not isinstance(document_id, str):
        raise InvalidCursor("Cursor fields must be strings.")
    try:
        created_at = datetime.fromisoformat(created_at_raw)
    except ValueError as exc:
        raise InvalidCursor(f"Cursor created_at is not an ISO datetime: {exc}") from exc
    return created_at, document_id


# How long a write may wait on a contended SQLite database before raising
# `database is locked`. 5 s is well above any healthy contention window in the
# MVP and short enough that a real deadlock surfaces before a request gateway
# times out.
_SQLITE_BUSY_TIMEOUT_MS = 5000


class CatalogStore(Protocol):
    """Persistence boundary for document catalog metadata."""

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        """Return the first version with the hash, if one exists."""

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        """Persist a newly created document family and first version."""

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        """Append a new version to an existing document family and update
        ``Document.latest_version_id``."""

    def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        """Return document families with their versions.

        Documents are returned sorted by ``(created_at ASC, id ASC)`` — the
        ``id`` tie-breaker keeps two same-second uploads from shifting
        between pages. When ``cursor`` is provided, only rows strictly
        greater than the encoded ``(created_at, id)`` tuple are returned.
        When ``limit`` is provided, at most ``limit`` rows are returned.
        Both are optional and default to "all rows from the start".

        Raises :class:`InvalidCursor` if ``cursor`` cannot be decoded.
        """

    def get_document(self, document_id: str) -> Document | None:
        """Return one document family with versions."""

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        """Return one version within a document family."""

    def update_version_status(
        self,
        document_id: str,
        version_id: str,
        status: DocumentVersionStatus,
    ) -> DocumentVersion:
        """Persist a lifecycle status change and return the updated version.

        The write is guarded by the predecessor set derived from the FSM:
        the row is only updated if its current status is one of the states
        ``status`` is reachable from. If the row's status no longer matches
        (because another writer raced ahead) the implementation raises
        :class:`IllegalTransition` and leaves the catalog untouched.
        """

    def update_version_failure(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        """Mark a version FAILED and persist a human-readable failure reason."""

    def update_version_review(
        self,
        document_id: str,
        version_id: str,
        status: ReviewedStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> DocumentVersion:
        """Atomically write a reviewer's decision: status (VALIDATED or
        REJECTED), the optional note, and the timestamp it was made."""

    # ------- Generated artefacts (raw extraction + semantic output) ------- #

    def save_raw_extraction(self, version_id: str, raw_extraction: RawExtraction) -> None:
        """Persist parser output for a version. Replaces any prior extraction."""

    def get_raw_extraction(self, version_id: str) -> RawExtraction:
        """Return the persisted raw extraction. Raises KeyError if none exists."""

    def save_semantic_document(self, version_id: str, semantic: SemanticDocument) -> None:
        """Persist semantic JSON (and rendered Markdown if any) for a version."""

    def get_semantic_document(self, version_id: str) -> SemanticDocument:
        """Return the persisted semantic document. Raises KeyError if none exists.

        Implementations route through ``semantic_schema_loader`` so older
        persisted payloads are migrated to the current shape before being
        returned. Per ADR-008.
        """

    def get_semantic_document_payload(self, version_id: str) -> dict:
        """Return the raw persisted JSON payload as a dict.

        This is the read-side counterpart to ``save_semantic_document``: it
        returns the bytes-on-disk shape (whatever ``schema_version`` they
        carry) without coercing them through the current Pydantic model, so
        callers can route them through the schema loader explicitly. Raises
        KeyError if none exists.
        """


class InMemoryCatalogStore:
    """In-memory catalog implementation for unit tests and fast local demos."""

    def __init__(self):
        self.documents: dict[str, Document] = {}
        self.versions_by_hash: dict[str, DocumentVersion] = {}
        self.versions: dict[str, DocumentVersion] = {}
        self.raw_extractions: dict[str, RawExtraction] = {}
        # Stored as raw JSON-shaped dicts (not typed SemanticDocument) so the
        # in-memory store mirrors the SQLite payload column and the loader
        # is the single boundary that yields a typed model. Per ADR-008.
        self.semantic_documents: dict[str, dict] = {}

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        return self.versions_by_hash.get(sha256)

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        self.documents[document.id] = document
        self.versions[version.id] = version
        if version.duplicate_of_version_id is None:
            self.versions_by_hash[version.sha256] = version

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError("Document not found.")
        document.versions.append(version)
        document.latest_version_id = version.id
        self.versions[version.id] = version
        if version.duplicate_of_version_id is None:
            self.versions_by_hash.setdefault(version.sha256, version)

    def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        ordered = sorted(
            self.documents.values(),
            key=lambda d: (d.created_at, d.id),
        )
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            ordered = [d for d in ordered if (d.created_at, d.id) > (after_created_at, after_id)]
        if limit is not None:
            ordered = ordered[:limit]
        return ordered

    def get_document(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        document = self.documents.get(document_id)
        if document is None:
            raise KeyError("Document not found.")
        for version in document.versions:
            if version.id == version_id:
                return version
        raise KeyError("Document version not found.")

    def update_version_status(
        self,
        document_id: str,
        version_id: str,
        status: DocumentVersionStatus,
    ) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        predecessors = ALLOWED_PREDECESSORS[status]
        if version.status not in predecessors:
            raise IllegalTransition(
                f"Cannot transition to {status.value}: expected current status in "
                f"{{{', '.join(sorted(s.value for s in predecessors))}}} "
                f"but found {version.status.value}."
            )
        version.status = status
        return version

    def update_version_failure(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        version.status = DocumentVersionStatus.FAILED
        version.failure_reason = reason
        return version

    def update_version_review(
        self,
        document_id: str,
        version_id: str,
        status: ReviewedStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        version.status = status
        version.reviewer_note = reviewer_note
        version.reviewed_at = reviewed_at
        return version

    def save_raw_extraction(self, version_id: str, raw_extraction: RawExtraction) -> None:
        self.raw_extractions[version_id] = raw_extraction

    def get_raw_extraction(self, version_id: str) -> RawExtraction:
        raw_extraction = self.raw_extractions.get(version_id)
        if raw_extraction is None:
            raise KeyError("Raw extraction not found.")
        return raw_extraction

    def save_semantic_document(self, version_id: str, semantic: SemanticDocument) -> None:
        self.semantic_documents[version_id] = semantic.model_dump(mode="json")

    def get_semantic_document(self, version_id: str) -> SemanticDocument:
        return load_semantic_document(self.get_semantic_document_payload(version_id))

    def get_semantic_document_payload(self, version_id: str) -> dict:
        payload = self.semantic_documents.get(version_id)
        if payload is None:
            raise KeyError("Semantic output not found.")
        # Deep copy so callers can't mutate persisted state.
        return copy.deepcopy(payload)


class SQLiteCatalogStore:
    """SQLite-backed catalog store for the local persistent MVP."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        # Excluding rows where `duplicate_of_version_id IS NOT NULL` matches
        # the in-memory store's behaviour (it never indexes duplicates by
        # hash) and prevents a third upload of the same bytes from chaining
        # off a duplicate row instead of pointing at the original version.
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE sha256 = ? AND duplicate_of_version_id IS NULL
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (sha256,),
            ).fetchone()
        return self._version_from_row(row) if row else None

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (id, original_filename, latest_version_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    document.id,
                    document.original_filename,
                    document.latest_version_id,
                    document.created_at.isoformat(),
                ),
            )
            self._insert_version(connection, version)

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        if self.get_document(document_id) is None:
            raise KeyError("Document not found.")
        with self._connect() as connection:
            self._insert_version(connection, version)
            connection.execute(
                "UPDATE documents SET latest_version_id = ? WHERE id = ?",
                (version.id, document_id),
            )

    def list_documents(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> list[Document]:
        # Build the documents query with optional cursor/limit clauses.
        # The tuple comparison `(created_at, id) > (?, ?)` is supported by
        # SQLite directly and matches the in-memory store's ordering.
        params: list[object] = []
        where_clause = ""
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            where_clause = " WHERE (created_at, id) > (?, ?)"
            params.extend([after_created_at.isoformat(), after_id])
        limit_clause = ""
        if limit is not None:
            limit_clause = " LIMIT ?"
            params.append(int(limit))
        query = (
            "SELECT * FROM documents"
            + where_clause
            + " ORDER BY created_at ASC, id ASC"
            + limit_clause
        )
        with self._connect() as connection:
            document_rows = connection.execute(query, tuple(params)).fetchall()
            if not document_rows:
                return []
            # Only fetch versions belonging to the slice we're returning so
            # we don't read the whole `document_versions` table per page.
            ids = [row["id"] for row in document_rows]
            placeholders = ", ".join("?" for _ in ids)
            version_rows = connection.execute(
                f"""
                SELECT * FROM document_versions
                WHERE document_id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                tuple(ids),
            ).fetchall()
        versions_by_document: dict[str, list[DocumentVersion]] = {}
        for row in version_rows:
            version = self._version_from_row(row)
            versions_by_document.setdefault(version.document_id, []).append(version)
        return [
            self._document_from_row(row, versions_by_document.get(row["id"], []))
            for row in document_rows
        ]

    def get_document(self, document_id: str) -> Document | None:
        with self._connect() as connection:
            document_row = connection.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if document_row is None:
                return None
            version_rows = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE document_id = ?
                ORDER BY created_at ASC
                """,
                (document_id,),
            ).fetchall()
        versions = [self._version_from_row(row) for row in version_rows]
        return self._document_from_row(document_row, versions)

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE document_id = ? AND id = ?
                """,
                (document_id, version_id),
            ).fetchone()
        if row is None:
            document_exists = self.get_document(document_id) is not None
            if not document_exists:
                raise KeyError("Document not found.")
            raise KeyError("Document version not found.")
        return self._version_from_row(row)

    def update_version_status(
        self,
        document_id: str,
        version_id: str,
        status: DocumentVersionStatus,
    ) -> DocumentVersion:
        # Confirm the row exists at all so a missing version still raises
        # KeyError ("Document not found." / "Document version not found.")
        # rather than masquerading as a concurrency conflict.
        self.get_version(document_id=document_id, version_id=version_id)
        predecessors = ALLOWED_PREDECESSORS[status]
        # Build an "?, ?, ..." placeholder list so the predecessor set is
        # bound as parameters (sqlite3 won't expand a list inside a single
        # placeholder). An empty predecessor set means the FSM has no edges
        # leading into ``status`` — the UPDATE will match zero rows and the
        # rowcount==0 branch below raises with a clear message.
        if predecessors:
            placeholders = ", ".join("?" for _ in predecessors)
            predecessor_values = tuple(s.value for s in predecessors)
        else:
            # SQLite rejects "IN ()". Use a sentinel that no real status
            # equals so the UPDATE matches zero rows by construction.
            placeholders = "?"
            predecessor_values = ("__no_legal_predecessor__",)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE document_versions
                SET status = ?
                WHERE document_id = ? AND id = ? AND status IN ({placeholders})
                """,
                (status.value, document_id, version_id, *predecessor_values),
            )
            if cursor.rowcount == 0:
                actual_row = connection.execute(
                    """
                    SELECT status FROM document_versions
                    WHERE document_id = ? AND id = ?
                    """,
                    (document_id, version_id),
                ).fetchone()
                actual_status = actual_row["status"] if actual_row else "<missing>"
                expected = (
                    ", ".join(sorted(s.value for s in predecessors)) if predecessors else "<none>"
                )
                raise IllegalTransition(
                    f"Cannot transition to {status.value}: expected current status in "
                    f"{{{expected}}} but found {actual_status}."
                )
        return self.get_version(document_id=document_id, version_id=version_id)

    def update_version_failure(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        self.get_version(document_id=document_id, version_id=version_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE document_versions
                SET status = ?, failure_reason = ?
                WHERE document_id = ? AND id = ?
                """,
                (DocumentVersionStatus.FAILED.value, reason, document_id, version_id),
            )
        return self.get_version(document_id=document_id, version_id=version_id)

    def update_version_review(
        self,
        document_id: str,
        version_id: str,
        status: ReviewedStatus,
        reviewer_note: str | None,
        reviewed_at: datetime,
    ) -> DocumentVersion:
        self.get_version(document_id=document_id, version_id=version_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE document_versions
                SET status = ?, reviewer_note = ?, reviewed_at = ?
                WHERE document_id = ? AND id = ?
                """,
                (status.value, reviewer_note, reviewed_at.isoformat(), document_id, version_id),
            )
        return self.get_version(document_id=document_id, version_id=version_id)

    def save_raw_extraction(self, version_id: str, raw_extraction: RawExtraction) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO raw_extractions (document_version_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(document_version_id) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    version_id,
                    raw_extraction.model_dump_json(),
                    raw_extraction.created_at.isoformat(),
                ),
            )

    def get_raw_extraction(self, version_id: str) -> RawExtraction:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM raw_extractions WHERE document_version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Raw extraction not found.")
        return RawExtraction.model_validate_json(row["payload"])

    def save_semantic_document(self, version_id: str, semantic: SemanticDocument) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO semantic_documents (document_version_id, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(document_version_id) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (
                    version_id,
                    semantic.model_dump_json(),
                    semantic.created_at.isoformat(),
                ),
            )

    def get_semantic_document(self, version_id: str) -> SemanticDocument:
        return load_semantic_document(self.get_semantic_document_payload(version_id))

    def _initialize(self) -> None:
        with self._connect() as connection:
            # WAL is per-database and survives restart, so set once here.
            # WAL allows a writer + readers concurrently and reduces
            # `database is locked` errors during the eventual extraction
            # worker / API request overlap.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    latest_version_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_versions (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_uri TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duplicate_of_version_id TEXT,
                    failure_reason TEXT,
                    reviewer_note TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                )
                """
            )
            # Add review columns to pre-existing databases that were created
            # before reviewer_note / reviewed_at were introduced. SQLite does
            # not support `ADD COLUMN IF NOT EXISTS`, so we inspect first.
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(document_versions)").fetchall()
            }
            if "reviewer_note" not in existing_columns:
                connection.execute("ALTER TABLE document_versions ADD COLUMN reviewer_note TEXT")
            if "reviewed_at" not in existing_columns:
                connection.execute("ALTER TABLE document_versions ADD COLUMN reviewed_at TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_document_versions_sha256
                ON document_versions (sha256)
                """
            )
            # Generated artefacts: one row per version, holding the full
            # Pydantic JSON payload. document_version_id is the PK because
            # each version has at most one extraction and at most one
            # semantic document; re-extraction overwrites in place.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_extractions (
                    document_version_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semantic_documents (
                    document_version_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (document_version_id) REFERENCES document_versions(id)
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit on success, and **always close**.

        Python's `sqlite3.Connection` is itself a context manager that
        commits on exit but does NOT release the underlying handle —
        relying on it leaks one file descriptor per call. Wrapping in
        an explicit `@contextmanager` closes the connection, enables
        foreign-key enforcement, and sets a busy timeout for every
        operation.
        """
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS}")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _insert_version(self, connection: sqlite3.Connection, version: DocumentVersion) -> None:
        connection.execute(
            """
            INSERT INTO document_versions (
                id,
                document_id,
                version_number,
                filename,
                content_type,
                file_size,
                sha256,
                storage_uri,
                status,
                duplicate_of_version_id,
                failure_reason,
                reviewer_note,
                reviewed_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version.id,
                version.document_id,
                version.version_number,
                version.filename,
                version.content_type,
                version.file_size,
                version.sha256,
                version.storage_uri,
                version.status.value,
                version.duplicate_of_version_id,
                version.failure_reason,
                version.reviewer_note,
                version.reviewed_at.isoformat() if version.reviewed_at else None,
                version.created_at.isoformat(),
            ),
        )

    def _document_from_row(self, row: sqlite3.Row, versions: list[DocumentVersion]) -> Document:
        return Document(
            id=row["id"],
            original_filename=row["original_filename"],
            latest_version_id=row["latest_version_id"],
            created_at=row["created_at"],
            versions=versions,
        )

    def _version_from_row(self, row: sqlite3.Row) -> DocumentVersion:
        return DocumentVersion(
            id=row["id"],
            document_id=row["document_id"],
            version_number=row["version_number"],
            filename=row["filename"],
            content_type=row["content_type"],
            file_size=row["file_size"],
            sha256=row["sha256"],
            storage_uri=row["storage_uri"],
            status=DocumentVersionStatus(row["status"]),
            duplicate_of_version_id=row["duplicate_of_version_id"],
            failure_reason=row["failure_reason"],
            reviewer_note=row["reviewer_note"],
            reviewed_at=row["reviewed_at"],
            created_at=row["created_at"],
        )

    def get_semantic_document_payload(self, version_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM semantic_documents WHERE document_version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Semantic output not found.")
        return json.loads(row["payload"])
