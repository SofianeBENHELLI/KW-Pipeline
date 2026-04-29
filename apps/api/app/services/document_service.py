from uuid import uuid4

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.services.catalog_store import CatalogStore, InMemoryCatalogStore
from app.services.hash_service import compute_sha256
from app.services.storage_service import StorageService


class DocumentService:
    """Catalog service for document families and immutable versions.

    Every upload is hashed, duplicate detection uses the hash instead of the
    filename, and duplicate versions point back to the first matching version.
    """

    def __init__(self, storage: StorageService, catalog: CatalogStore | None = None):
        self.storage = storage
        self.catalog = catalog or InMemoryCatalogStore()

    def upload(self, filename: str, content_type: str, content: bytes) -> DocumentVersion:
        """Store uploaded bytes and return the cataloged document version."""
        digest = compute_sha256(content)
        duplicate = self.catalog.find_version_by_hash(digest)
        document_id = str(uuid4())
        version_id = str(uuid4())
        storage_uri = self.storage.put(f"documents/{version_id}/{filename}", content)
        status = DocumentVersionStatus.DUPLICATE_DETECTED if duplicate else DocumentVersionStatus.STORED
        version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=1,
            filename=filename,
            content_type=content_type,
            file_size=len(content),
            sha256=digest,
            storage_uri=storage_uri,
            status=status,
            duplicate_of_version_id=duplicate.id if duplicate else None,
        )
        document = Document.with_first_version(version)
        self.catalog.save_document_with_version(document=document, version=version)
        return version

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

    def mark_semantic_ready(self, document_id: str, version_id: str) -> DocumentVersion:
        """Mark generated semantic output as requiring human review."""
        return self.update_status(document_id, version_id, DocumentVersionStatus.NEEDS_REVIEW)
