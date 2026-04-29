from app.models.document import DocumentVersionStatus
from app.services.document_service import DocumentService
from app.services.storage_service import InMemoryStorageService


def test_upload_stores_document_metadata_and_bytes():
    service = DocumentService(storage=InMemoryStorageService())

    version = service.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Policy content",
    )

    document = service.get_document(version.document_id)
    assert document is not None
    assert document.original_filename == "policy.txt"
    assert version.status == DocumentVersionStatus.STORED
    assert service.storage.get(version.storage_uri) == b"Policy content"


def test_upload_detects_duplicate_by_hash_not_filename():
    service = DocumentService(storage=InMemoryStorageService())
    first = service.upload("policy.txt", "text/plain", b"same content")

    duplicate = service.upload("renamed.txt", "text/plain", b"same content")

    assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED
    assert duplicate.duplicate_of_version_id == first.id
