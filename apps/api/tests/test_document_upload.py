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


class TestUploadStream:
    def test_upload_stream_produces_same_digest_as_upload(self):
        service_a = DocumentService(storage=InMemoryStorageService())
        service_b = DocumentService(storage=InMemoryStorageService())
        payload = b"streaming versus contiguous"

        whole = service_a.upload("p.txt", "text/plain", payload)
        streamed = service_b.upload_stream(
            "p.txt", "text/plain", iter([payload[:10], payload[10:]])
        )

        assert streamed.sha256 == whole.sha256
        assert streamed.file_size == whole.file_size
        assert streamed.status == DocumentVersionStatus.STORED

    def test_upload_stream_round_trips_bytes_through_storage(self):
        service = DocumentService(storage=InMemoryStorageService())
        payload = b"chunk one|chunk two|chunk three"
        chunks = [b"chunk one|", b"chunk two|", b"chunk three"]

        version = service.upload_stream("p.txt", "text/plain", iter(chunks))

        assert service.storage.get(version.storage_uri) == payload
        assert version.file_size == len(payload)

    def test_upload_stream_detects_duplicate_by_hash(self):
        service = DocumentService(storage=InMemoryStorageService())
        first = service.upload("p.txt", "text/plain", b"shared")

        duplicate = service.upload_stream("renamed.txt", "text/plain", iter([b"sha", b"red"]))

        assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED
        assert duplicate.duplicate_of_version_id == first.id

    def test_upload_stream_appends_version_when_document_id_given(self):
        service = DocumentService(storage=InMemoryStorageService())
        v1 = service.upload("p.txt", "text/plain", b"v1 bytes")

        v2 = service.upload_stream(
            "p.txt",
            "text/plain",
            iter([b"v2 ", b"bytes"]),
            document_id=v1.document_id,
        )

        assert v2.document_id == v1.document_id
        assert v2.version_number == 2
        assert v2.status == DocumentVersionStatus.STORED

    def test_upload_stream_with_unknown_document_id_raises(self):
        service = DocumentService(storage=InMemoryStorageService())

        with pytest.raises(KeyError, match="Document not found"):
            service.upload_stream(
                "p.txt",
                "text/plain",
                iter([b"x"]),
                document_id="missing",
            )

    def test_upload_stream_handles_empty_iterable(self):
        # Empty payload still produces the published SHA-256 vector.
        service = DocumentService(storage=InMemoryStorageService())

        version = service.upload_stream("empty.txt", "text/plain", iter(()))

        assert version.sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert version.file_size == 0
