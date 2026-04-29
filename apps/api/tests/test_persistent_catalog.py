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


def test_persistent_versioned_upload_survives_restart(tmp_path):
    first_services = build_persistent_services(tmp_path)
    v1 = first_services.documents.upload("policy.txt", "text/plain", b"v1 bytes")
    v2 = first_services.documents.upload(
        "policy.txt", "text/plain", b"v2 bytes", document_id=v1.document_id
    )

    second_services = build_persistent_services(tmp_path)
    document = second_services.documents.get_document(v1.document_id)

    assert document is not None
    assert [v.id for v in document.versions] == [v1.id, v2.id]
    assert document.latest_version_id == v2.id
    assert document.versions[1].version_number == 2


def test_persistent_versioned_upload_to_unknown_document_raises(tmp_path):
    services = build_persistent_services(tmp_path)

    try:
        services.documents.upload(
            "p.txt", "text/plain", b"x", document_id="never-existed"
        )
    except KeyError as exc:
        assert "Document not found" in str(exc)
    else:
        raise AssertionError("Expected KeyError for missing document.")


def test_persistent_review_decision_survives_restart(tmp_path):
    first_services = build_persistent_services(tmp_path)
    uploaded = first_services.documents.upload(
        "policy.txt", "text/plain", b"to review"
    )
    # Drive the version into NEEDS_REVIEW so we can transition out of it.
    first_services.documents.update_status(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        status=DocumentVersionStatus.NEEDS_REVIEW,
    )

    first_services.documents.mark_validated(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        reviewer_note="checked the lineage",
    )

    second_services = build_persistent_services(tmp_path)
    version = second_services.documents.get_version(uploaded.document_id, uploaded.id)

    assert version.status == DocumentVersionStatus.VALIDATED
    assert version.reviewer_note == "checked the lineage"
    assert version.reviewed_at is not None


def test_sqlite_initialize_adds_review_columns_to_legacy_schema(tmp_path):
    """Databases created before reviewer_note / reviewed_at existed must be
    forward-migrated automatically the next time SQLiteCatalogStore is built."""
    import sqlite3

    from app.services.catalog_store import SQLiteCatalogStore

    db_path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            latest_version_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE document_versions (
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
            created_at TEXT NOT NULL
        );
        """
    )
    legacy.commit()
    legacy.close()

    SQLiteCatalogStore(db_path)

    inspector = sqlite3.connect(db_path)
    columns = {row[1] for row in inspector.execute("PRAGMA table_info(document_versions)").fetchall()}
    inspector.close()

    assert "reviewer_note" in columns
    assert "reviewed_at" in columns


def test_sqlite_append_to_missing_document_raises_directly(tmp_path):
    """The store-level guard fires when callers bypass DocumentService and
    hand a missing document_id straight to the catalog."""
    from app.models.document import DocumentVersionStatus
    from app.schemas.document import DocumentVersion
    from app.services.catalog_store import SQLiteCatalogStore

    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
    orphan = DocumentVersion(
        id="ver-1",
        document_id="ghost-doc",
        version_number=1,
        filename="p.txt",
        content_type="text/plain",
        file_size=1,
        sha256="a" * 64,
        storage_uri="file:///tmp/ignored",
        status=DocumentVersionStatus.STORED,
    )

    try:
        store.append_version_to_document(document_id="ghost-doc", version=orphan)
    except KeyError as exc:
        assert "Document not found" in str(exc)
    else:
        raise AssertionError("Expected KeyError for missing document.")


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
