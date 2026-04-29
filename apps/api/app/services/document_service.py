from uuid import uuid4

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.services.catalog_store import CatalogStore, InMemoryCatalogStore
from app.services.hash_service import compute_sha256
from app.services.storage_service import StorageService, safe_storage_key


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
            return self._upload_new_family(filename, content_type, content)

        existing_document = self.catalog.get_document(document_id)
        if existing_document is None:
            raise KeyError("Document not found.")
        return self._append_new_version(existing_document, filename, content_type, content)

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
        self.catalog.append_version_to_document(
            document_id=existing_document.id, version=version
        )
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
        """Return all cataloged document families."""
        return self.catalog.list_documents()

    def get_document(self, document_id: str) -> Document | None:
        """Return a document family by ID, or `None` when absent."""
        return self.catalog.get_document(document_id)

    def get_version(self, document_id: str, version_id: str) -> DocumentVersion:
        """Return a specific version within a document family."""
        return self.catalog.get_version(document_id=document_id, version_id=version_id)

    def update_status(self, document_id: str, version_id: str, status: DocumentVersionStatus) -> DocumentVersion:
        """Update and return a document version lifecycle status."""
        return self.catalog.update_version_status(
            document_id=document_id,
            version_id=version_id,
            status=status,
        )

    def mark_failed(
        self,
        document_id: str,
        version_id: str,
        reason: str,
    ) -> DocumentVersion:
        """Mark a version FAILED and persist the human-readable failure reason."""
        return self.catalog.update_version_failure(
            document_id=document_id,
            version_id=version_id,
            reason=reason,
        )

    def mark_semantic_ready(self, document_id: str, version_id: str) -> DocumentVersion:
        """Mark generated semantic output as requiring human review."""
        return self.update_status(document_id, version_id, DocumentVersionStatus.NEEDS_REVIEW)
