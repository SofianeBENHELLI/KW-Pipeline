import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion

ReviewedStatus = DocumentVersionStatus  # narrowed to VALIDATED | REJECTED at the call site


class CatalogStore(Protocol):
    """Persistence boundary for document catalog metadata."""

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        """Return the first version with the hash, if one exists."""

    def save_document_with_version(self, document: Document, version: DocumentVersion) -> None:
        """Persist a newly created document family and first version."""

    def append_version_to_document(self, document_id: str, version: DocumentVersion) -> None:
        """Append a new version to an existing document family and update
        ``Document.latest_version_id``."""

    def list_documents(self) -> list[Document]:
        """Return all document families with their versions."""

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
        """Persist a lifecycle status change and return the updated version."""

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


class InMemoryCatalogStore:
    """In-memory catalog implementation for unit tests and fast local demos."""

    def __init__(self):
        self.documents: dict[str, Document] = {}
        self.versions_by_hash: dict[str, DocumentVersion] = {}
        self.versions: dict[str, DocumentVersion] = {}

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

    def list_documents(self) -> list[Document]:
        return list(self.documents.values())

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


class SQLiteCatalogStore:
    """SQLite-backed catalog store for the local persistent MVP."""

    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def find_version_by_hash(self, sha256: str) -> DocumentVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE sha256 = ?
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

    def list_documents(self) -> list[Document]:
        with self._connect() as connection:
            document_rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at ASC"
            ).fetchall()
            version_rows = connection.execute(
                "SELECT * FROM document_versions ORDER BY created_at ASC"
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
        self.get_version(document_id=document_id, version_id=version_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE document_versions
                SET status = ?
                WHERE document_id = ? AND id = ?
                """,
                (status.value, document_id, version_id),
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

    def _initialize(self) -> None:
        with self._connect() as connection:
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
                connection.execute(
                    "ALTER TABLE document_versions ADD COLUMN reviewer_note TEXT"
                )
            if "reviewed_at" not in existing_columns:
                connection.execute(
                    "ALTER TABLE document_versions ADD COLUMN reviewed_at TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_document_versions_sha256
                ON document_versions (sha256)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

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
