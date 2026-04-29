from uuid import uuid4

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.services.hash_service import compute_sha256
from app.services.storage_service import InMemoryStorageService


class DocumentService:
    def __init__(self, storage: InMemoryStorageService):
        self.storage = storage
        self.documents: dict[str, Document] = {}
        self.versions_by_hash: dict[str, DocumentVersion] = {}
        self.versions: dict[str, DocumentVersion] = {}

    def upload(self, filename: str, content_type: str, content: bytes) -> DocumentVersion:
        digest = compute_sha256(content)
        duplicate = self.versions_by_hash.get(digest)
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
        self.documents[document_id] = document
        self.versions[version_id] = version
        if duplicate is None:
            self.versions_by_hash[digest] = version
        return version

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

    def update_status(self, document_id: str, version_id: str, status: DocumentVersionStatus) -> DocumentVersion:
        version = self.get_version(document_id=document_id, version_id=version_id)
        version.status = status
        return version

    def mark_semantic_ready(self, document_id: str, version_id: str) -> DocumentVersion:
        return self.update_status(document_id, version_id, DocumentVersionStatus.NEEDS_REVIEW)
