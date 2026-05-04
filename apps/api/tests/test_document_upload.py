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


class TestDuplicateUploadAppendsToOriginalFamily:
    """Issue #59: anonymous duplicate uploads stay inside the original family.

    ADR-002 says every upload is a ``DocumentVersion`` within a stable
    ``Document`` identity. An anonymous upload (no ``document_id``) whose
    bytes match an existing version must therefore be appended to the
    matching version's family as v2/v3/... not spawn a new family at v1.
    """

    def test_duplicate_anonymous_upload_appends_v2_to_original_family(self):
        service = DocumentService(storage=InMemoryStorageService())
        first = service.upload("policy.txt", "text/plain", b"same content")

        duplicate = service.upload("renamed.txt", "text/plain", b"same content")

        # Same family, not a new family.
        assert duplicate.document_id == first.document_id
        assert duplicate.version_number == 2
        assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED
        assert duplicate.duplicate_of_version_id == first.id

        # Catalog has exactly one family with both versions.
        documents = service.list_documents()
        assert len(documents) == 1
        assert [v.id for v in documents[0].versions] == [first.id, duplicate.id]

    def test_three_consecutive_duplicate_uploads_yield_one_family_three_versions(self):
        service = DocumentService(storage=InMemoryStorageService())
        v1 = service.upload("a.txt", "text/plain", b"shared bytes")
        v2 = service.upload("b.txt", "text/plain", b"shared bytes")
        v3 = service.upload("c.txt", "text/plain", b"shared bytes")

        documents = service.list_documents()
        assert len(documents) == 1
        family = documents[0]
        assert len(family.versions) == 3

        assert family.versions[0].version_number == 1
        assert family.versions[0].status != DocumentVersionStatus.DUPLICATE_DETECTED

        assert family.versions[1].version_number == 2
        assert family.versions[1].status == DocumentVersionStatus.DUPLICATE_DETECTED
        assert family.versions[1].duplicate_of_version_id == v1.id

        # Both stores' ``find_version_by_hash`` excludes duplicate rows
        # and returns the oldest non-duplicate match, so v3 also points
        # at v1 (not at v2). This keeps the duplicate chain flat.
        assert family.versions[2].version_number == 3
        assert family.versions[2].status == DocumentVersionStatus.DUPLICATE_DETECTED
        assert family.versions[2].duplicate_of_version_id == v1.id

        # IDs returned to callers match the catalog state.
        assert {v1.id, v2.id, v3.id} == {v.id for v in family.versions}

    def test_distinct_bytes_still_create_separate_families(self):
        """Regression: the no-document_id path only stitches families when
        the bytes are an actual duplicate. Distinct bytes still spawn a
        fresh family at v1."""
        service = DocumentService(storage=InMemoryStorageService())

        a = service.upload("a.txt", "text/plain", b"alpha")
        b = service.upload("b.txt", "text/plain", b"bravo")

        assert a.document_id != b.document_id
        assert a.version_number == 1
        assert b.version_number == 1
        assert len(service.list_documents()) == 2

    def test_explicit_document_id_still_appends_normally(self):
        """Regression: the explicit-document_id path is untouched by #59."""
        service = DocumentService(storage=InMemoryStorageService())
        v1 = service.upload("p.txt", "text/plain", b"bytes one")

        v2 = service.upload("p.txt", "text/plain", b"bytes two", document_id=v1.document_id)
        v3 = service.upload("p.txt", "text/plain", b"bytes three", document_id=v1.document_id)

        assert v1.document_id == v2.document_id == v3.document_id
        assert (v1.version_number, v2.version_number, v3.version_number) == (1, 2, 3)
        assert v2.status == DocumentVersionStatus.STORED
        assert v3.status == DocumentVersionStatus.STORED

    def test_upload_stream_duplicate_appends_to_original_family(self):
        service = DocumentService(storage=InMemoryStorageService())
        first = service.upload("p.txt", "text/plain", b"shared")

        duplicate = service.upload_stream("renamed.txt", "text/plain", iter([b"sha", b"red"]))

        assert duplicate.document_id == first.document_id
        assert duplicate.version_number == 2
        assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED
        assert duplicate.duplicate_of_version_id == first.id
        assert len(service.list_documents()) == 1

    def test_three_streamed_duplicates_yield_one_family_three_versions(self):
        service = DocumentService(storage=InMemoryStorageService())
        payload = b"streamed bytes"

        v1 = service.upload_stream("a.txt", "text/plain", iter([payload]))
        v2 = service.upload_stream("b.txt", "text/plain", iter([payload[:5], payload[5:]]))
        v3 = service.upload_stream("c.txt", "text/plain", iter([payload[:1], payload[1:]]))

        documents = service.list_documents()
        assert len(documents) == 1
        family = documents[0]
        assert [v.version_number for v in family.versions] == [1, 2, 3]
        assert family.versions[0].id == v1.id
        assert family.versions[1].id == v2.id
        assert family.versions[2].id == v3.id
        assert family.versions[1].duplicate_of_version_id == v1.id
        assert family.versions[2].duplicate_of_version_id == v1.id


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
