import pytest

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


def test_upload_unsafe_filename_does_not_break_storage_key():
    """Filenames with path traversal or null bytes must not propagate to the
    storage URI (the user-facing filename is preserved separately)."""
    service = DocumentService(storage=InMemoryStorageService())

    version = service.upload("../etc/passwd", "text/plain", b"root:x:0:0::/:/bin/sh")

    # Display filename is preserved exactly.
    assert version.filename == "../etc/passwd"
    # Storage URI is sanitized — basename only, no path traversal.
    assert "/passwd" in version.storage_uri
    assert ".." not in version.storage_uri
    # Bytes round-trip via the sanitized URI.
    assert service.storage.get(version.storage_uri) == b"root:x:0:0::/:/bin/sh"


class TestUploadAppendsVersion:
    def test_uploading_with_document_id_appends_a_v2_in_the_same_family(self):
        service = DocumentService(storage=InMemoryStorageService())
        v1 = service.upload("policy.txt", "text/plain", b"version one")

        v2 = service.upload(
            filename="policy.txt",
            content_type="text/plain",
            content=b"version two",
            document_id=v1.document_id,
        )

        assert v2.document_id == v1.document_id
        assert v2.id != v1.id
        assert v2.version_number == 2
        assert v2.status == DocumentVersionStatus.STORED

        document = service.get_document(v1.document_id)
        assert [v.id for v in document.versions] == [v1.id, v2.id]
        assert document.latest_version_id == v2.id

    def test_consecutive_versioned_uploads_increment_version_number(self):
        service = DocumentService(storage=InMemoryStorageService())
        v1 = service.upload("policy.txt", "text/plain", b"one")
        v2 = service.upload("policy.txt", "text/plain", b"two", document_id=v1.document_id)
        v3 = service.upload("policy.txt", "text/plain", b"three", document_id=v1.document_id)

        assert (v1.version_number, v2.version_number, v3.version_number) == (1, 2, 3)

    def test_versioned_upload_to_unknown_document_raises_keyerror(self):
        service = DocumentService(storage=InMemoryStorageService())

        with pytest.raises(KeyError, match="Document not found"):
            service.upload(
                "policy.txt",
                "text/plain",
                b"content",
                document_id="unknown-document-id",
            )

    def test_versioned_upload_with_duplicate_bytes_still_marks_duplicate(self):
        """Duplicate-by-hash detection wins over version creation: even when
        appending to an existing family, matching bytes produce a
        DUPLICATE_DETECTED version pointing back at the original."""
        service = DocumentService(storage=InMemoryStorageService())
        original = service.upload("policy.txt", "text/plain", b"shared bytes")

        duplicate = service.upload(
            "policy_renamed.txt",
            "text/plain",
            b"shared bytes",
            document_id=original.document_id,
        )

        assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED
        assert duplicate.duplicate_of_version_id == original.id
        # Version number still bumps even when marked duplicate — the upload
        # event itself is recorded.
        assert duplicate.version_number == 2

    def test_versioned_upload_can_use_a_different_filename(self):
        service = DocumentService(storage=InMemoryStorageService())
        v1 = service.upload("draft.txt", "text/plain", b"first")

        v2 = service.upload("final.txt", "text/plain", b"second", document_id=v1.document_id)

        document = service.get_document(v1.document_id)
        # `original_filename` on the family stays the first filename uploaded;
        # individual versions can carry their own filename.
        assert document.original_filename == "draft.txt"
        assert v2.filename == "final.txt"
