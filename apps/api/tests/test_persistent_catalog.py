from app.dependencies import build_persistent_services
from app.models.document import DocumentVersionStatus
from app.services.storage_service import FileSystemStorageService


def test_persistent_services_keep_catalog_and_raw_file_across_restarts(tmp_path):
    first_services = build_persistent_services(tmp_path)
    uploaded = first_services.documents.upload(
        filename="policy.txt",
        content_type="text/plain",
        content=b"Persistent policy",
    )

    second_services = build_persistent_services(tmp_path)
    document = second_services.documents.get_document(uploaded.document_id)
    version = second_services.documents.get_version(uploaded.document_id, uploaded.id)

    assert document is not None
    assert document.versions[0].id == uploaded.id
    assert version.status == DocumentVersionStatus.STORED
    assert second_services.storage.get(version.storage_uri) == b"Persistent policy"


def test_persistent_duplicate_detection_uses_existing_hash_after_restart(tmp_path):
    first_services = build_persistent_services(tmp_path)
    original = first_services.documents.upload("first.txt", "text/plain", b"same bytes")

    second_services = build_persistent_services(tmp_path)
    duplicate = second_services.documents.upload("second.txt", "text/plain", b"same bytes")

    assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED
    assert duplicate.duplicate_of_version_id == original.id


def test_persistent_status_updates_survive_restart(tmp_path):
    first_services = build_persistent_services(tmp_path)
    uploaded = first_services.documents.upload("status.txt", "text/plain", b"status")

    first_services.documents.update_status(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        status=DocumentVersionStatus.EXTRACTED,
    )

    second_services = build_persistent_services(tmp_path)
    version = second_services.documents.get_version(uploaded.document_id, uploaded.id)

    assert version.status == DocumentVersionStatus.EXTRACTED


def test_persistent_catalog_raises_clear_errors_for_missing_records(tmp_path):
    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("known.txt", "text/plain", b"known")

    try:
        services.documents.get_version("missing-document", uploaded.id)
    except KeyError as exc:
        assert str(exc) == "'Document not found.'"
    else:
        raise AssertionError("Expected missing document lookup to fail.")

    try:
        services.documents.get_version(uploaded.document_id, "missing-version")
    except KeyError as exc:
        assert str(exc) == "'Document version not found.'"
    else:
        raise AssertionError("Expected missing version lookup to fail.")


def test_persistent_failure_reason_survives_restart(tmp_path):
    first_services = build_persistent_services(tmp_path)
    uploaded = first_services.documents.upload("doomed.txt", "text/plain", b"x")

    first_services.documents.mark_failed(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        reason="PlainTextParser: simulated parser failure",
    )

    second_services = build_persistent_services(tmp_path)
    version = second_services.documents.get_version(uploaded.document_id, uploaded.id)

    assert version.status == DocumentVersionStatus.FAILED
    assert version.failure_reason == "PlainTextParser: simulated parser failure"


def test_filesystem_storage_rejects_parent_traversal(tmp_path):
    storage = FileSystemStorageService(tmp_path)

    try:
        storage.put("../escape.txt", b"nope")
    except ValueError as exc:
        assert "parent traversal" in str(exc)
    else:
        raise AssertionError("Expected parent traversal to be rejected.")


def test_filesystem_storage_rejects_file_uri_outside_root(tmp_path):
    storage = FileSystemStorageService(tmp_path / "storage")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_bytes(b"outside")

    try:
        storage.get(outside_file.resolve().as_uri())
    except ValueError as exc:
        assert "outside the configured root" in str(exc)
    else:
        raise AssertionError("Expected outside file URI to be rejected.")
