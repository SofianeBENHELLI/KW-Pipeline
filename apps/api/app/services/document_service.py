import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from app.models.document import DocumentVersionStatus, assert_transition
from app.schemas.document import Document, DocumentVersion
from app.services.catalog_store import (
    CatalogStore,
    InMemoryCatalogStore,
    _encode_cursor,
)
from app.services.hash_service import compute_sha256
from app.services.storage_service import StorageService, safe_storage_key

log = logging.getLogger(__name__)


class DocumentService:
    """Catalog service for document families and immutable versions.

    Every upload is hashed, duplicate detection uses the hash instead of the
    filename, and duplicate versions point back to the first matching version.
    """

    def __init__(self, storage: StorageService, catalog: CatalogStore | None = None):
        self.storage = storage
        self.catalog = catalog or InMemoryCatalogStore()

    def upload(
        self,
        filename: str,
        content_type: str,
        content: bytes,
        document_id: str | None = None,
    ) -> DocumentVersion:
        """Store uploaded bytes and return the cataloged document version.

        When ``document_id`` is provided, the upload is appended to the
        existing document family as a new version (``version_number =
        max(existing) + 1``). Without it, a new document family is created
        with ``version_number = 1``. Hash-based duplicate detection runs in
        both cases and wins over version creation: matching bytes always
        produce a ``DUPLICATE_DETECTED`` version pointing at the original.
        """
        if document_id is None:
            version = self._upload_new_family(filename, content_type, content)
        else:
            existing_document = self.catalog.get_document(document_id)
            if existing_document is None:
                raise KeyError("Document not found.")
            version = self._append_new_version(existing_document, filename, content_type, content)
        _log_uploaded(version)
        return version

    def upload_stream(
        self,
        filename: str,
        content_type: str,
        chunks: Iterable[bytes],
        document_id: str | None = None,
    ) -> DocumentVersion:
        """Streaming sibling of :meth:`upload` for chunk-iterable callers.

        The chunk iterator is consumed exactly once: each chunk is hashed
        and forwarded to storage in lockstep, so peak memory tracks the
        chunk size (typically 8 MiB) instead of the full payload. The
        resulting digest is byte-identical to ``upload(joined_bytes)``.
        """
        if document_id is not None:
            existing_document = self.catalog.get_document(document_id)
            if existing_document is None:
                raise KeyError("Document not found.")
            target_document = existing_document
        else:
            target_document = None

        version_id = str(uuid4())
        digest_obj = sha256()
        total_size = 0

        def _hash_and_count(source: Iterable[bytes]) -> Iterable[bytes]:
            nonlocal total_size
            for chunk in source:
                digest_obj.update(chunk)
                total_size += len(chunk)
                yield chunk

        storage_uri = self.storage.put_stream(
            safe_storage_key(version_id, filename), _hash_and_count(chunks)
        )
        digest = digest_obj.hexdigest()
        duplicate = self.catalog.find_version_by_hash(digest)

        if target_document is None:
            document_id_value = str(uuid4())
            version_number = 1
        else:
            document_id_value = target_document.id
            version_number = (
                max((v.version_number for v in target_document.versions), default=0) + 1
            )

        status = (
            DocumentVersionStatus.DUPLICATE_DETECTED if duplicate else DocumentVersionStatus.STORED
        )
        version = DocumentVersion(
            id=version_id,
            document_id=document_id_value,
            version_number=version_number,
            filename=filename,
            content_type=content_type,
            file_size=total_size,
            sha256=digest,
            storage_uri=storage_uri,
            status=status,
            duplicate_of_version_id=duplicate.id if duplicate else None,
        )

        if target_document is None:
            document = Document.with_first_version(version)
            self.catalog.save_document_with_version(document=document, version=version)
        else:
            self.catalog.append_version_to_document(document_id=target_document.id, version=version)
        _log_uploaded(version)
        return version

    def _upload_new_family(
        self,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> DocumentVersion:
        digest = compute_sha256(content)
        duplicate = self.catalog.find_version_by_hash(digest)
        document_id = str(uuid4())
        version = self._build_version(
            document_id=document_id,
            version_number=1,
            filename=filename,
            content_type=content_type,
            content=content,
            digest=digest,
            duplicate=duplicate,
        )
        document = Document.with_first_version(version)
        self.catalog.save_document_with_version(document=document, version=version)
        return version

    def _append_new_version(
        self,
        existing_document: Document,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> DocumentVersion:
        digest = compute_sha256(content)
        duplicate = self.catalog.find_version_by_hash(digest)
        next_version_number = (
            max((v.version_number for v in existing_document.versions), default=0) + 1
        )
        version = self._build_version(
            document_id=existing_document.id,
            version_number=next_version_number,
            filename=filename,
            content_type=content_type,
            content=content,
            digest=digest,
            duplicate=duplicate,
        )
        self.catalog.append_version_to_document(document_id=existing_document.id, version=version)
        return version

    def _build_version(
        self,
        *,
        document_id: str,
        version_number: int,
        filename: str,
        content_type: str,
        content: bytes,
        digest: str,
        duplicate: DocumentVersion | None,
    ) -> DocumentVersion:
        version_id = str(uuid4())
        storage_uri = self.storage.put(safe_storage_key(version_id, filename), content)
        status = (
            DocumentVersionStatus.DUPLICATE_DETECTED if duplicate else DocumentVersionStatus.STORED
        )
        return DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=version_number,
            filename=filename,
            content_type=content_type,
            file_size=len(content),
            sha256=digest,
            storage_uri=storage_uri,
            status=status,
            duplicate_of_version_id=duplicate.id if duplicate else None,
        )

    def list_documents(self) -> list[Document]:
        """Return all cataloged document families.

        Kept for in-process callers that want every document in catalog
        order. The HTTP route uses :meth:`list_documents_page` to paginate.
        """
        return self.catalog.list_documents()

    def list_documents_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[Document], str | None]:
        """Return one page of documents and the cursor for the next page.

        ``next_cursor`` is ``None`` when the page is short — i.e. the
        underlying store returned fewer than ``limit`` rows, which means
        there is no more data to walk. Otherwise the cursor encodes the
        last returned row's ``(created_at, id)`` so the next call returns
        rows strictly greater than that tuple.

        Raises :class:`InvalidCursor` if ``cursor`` cannot be decoded; the
        route layer maps that to HTTP 400.
        """
        items = self.catalog.list_documents(cursor=cursor, limit=limit)
        if len(items) < limit:
            return items, None
        last = items[-1]
        return items, _encode_cursor((last.created_at, last.id))

    def get_document(self, document_id: str) -> Document | None:
        """Return a document family by ID, or `None` when absent."""
        return self.catalog.get_document(document_id)

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        """Return a specific version within a document family."""
        return self.catalog.get_version(document_id=document_id, version_id=version_id)

    def update_status(
        self, document_id: str, version_id: str, status: DocumentVersionStatus
    ) -> DocumentVersion:
        """Update and return a document version lifecycle status.

        The transition from the version's current status to ``status`` is
        validated against ``ALLOWED_TRANSITIONS``; an illegal transition
        raises ``ValueError`` and the catalog is left untouched. ``mark_failed``,
        ``mark_validated``, and ``mark_rejected`` enforce their own preconditions
        and bypass this helper deliberately.

        Every successful FSM move emits a ``document.status_changed``
        audit event (issue #42); call sites that bypass this helper
        (``mark_failed``/``_record_review``) emit the same event from
        their own paths so a grep for the event name returns every
        transition the catalog recorded.
        """
        version = self.catalog.get_version(document_id=document_id, version_id=version_id)
        assert_transition(version.status, status)
        previous = version.status
        updated = self.catalog.update_version_status(
            document_id=document_id,
            version_id=version_id,
            status=status,
        )
        _log_status_changed(updated, previous=previous)
        return updated

    def mark_failed(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        """Mark a version FAILED and persist the human-readable failure reason."""
        previous = self.catalog.get_version(document_id=document_id, version_id=version_id).status
        updated = self.catalog.update_version_failure(
            document_id=document_id,
            version_id=version_id,
            reason=reason,
        )
        _log_status_changed(updated, previous=previous)
        return updated

    def mark_semantic_ready(self, document_id: str, version_id: str) -> DocumentVersion:
        """Mark generated semantic output as requiring human review."""
        return self.update_status(document_id, version_id, DocumentVersionStatus.NEEDS_REVIEW)

    def mark_validated(
        self,
        document_id: str,
        version_id: str,
        reviewer_note: str | None = None,
    ) -> DocumentVersion:
        """Reviewer accepts the semantic output. Refuses transition unless the
        version is currently in NEEDS_REVIEW."""
        return self._record_review(
            document_id=document_id,
            version_id=version_id,
            target_status=DocumentVersionStatus.VALIDATED,
            reviewer_note=reviewer_note,
        )

    def mark_rejected(
        self,
        document_id: str,
        version_id: str,
        reviewer_note: str | None = None,
    ) -> DocumentVersion:
        """Reviewer rejects the semantic output. Refuses transition unless the
        version is currently in NEEDS_REVIEW."""
        return self._record_review(
            document_id=document_id,
            version_id=version_id,
            target_status=DocumentVersionStatus.REJECTED,
            reviewer_note=reviewer_note,
        )

    def _record_review(
        self,
        *,
        document_id: str,
        version_id: str,
        target_status: DocumentVersionStatus,
        reviewer_note: str | None,
    ) -> DocumentVersion:
        version = self.catalog.get_version(document_id=document_id, version_id=version_id)
        if version.status != DocumentVersionStatus.NEEDS_REVIEW:
            raise ValueError(
                f"Version is in {version.status.value}, not NEEDS_REVIEW; "
                f"cannot transition to {target_status.value}."
            )
        previous = version.status
        updated = self.catalog.update_version_review(
            document_id=document_id,
            version_id=version_id,
            status=target_status,
            reviewer_note=reviewer_note,
            reviewed_at=datetime.now(UTC),
        )
        _log_status_changed(updated, previous=previous)
        if target_status == DocumentVersionStatus.VALIDATED:
            log.info(
                "review.validated",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "reviewer_note": reviewer_note,
                },
            )
        else:
            log.info(
                "review.rejected",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "reviewer_note": reviewer_note,
                },
            )
        return updated


def _log_uploaded(version: DocumentVersion) -> None:
    """Emit a ``document.uploaded`` audit event for a fresh version."""
    log.info(
        "document.uploaded",
        extra={
            "document_id": version.document_id,
            "version_id": version.id,
            "version_number": version.version_number,
            "sha256": version.sha256,
            "bytes": version.file_size,
            "content_type": version.content_type,
            "filename": version.filename,
            "is_duplicate": (version.status == DocumentVersionStatus.DUPLICATE_DETECTED),
        },
    )


def _log_status_changed(
    version: DocumentVersion,
    *,
    previous: DocumentVersionStatus,
) -> None:
    """Emit a ``document.status_changed`` audit event for an FSM move."""
    log.info(
        "document.status_changed",
        extra={
            "document_id": version.document_id,
            "version_id": version.id,
            "from": previous.value,
            "to": version.status.value,
        },
    )
