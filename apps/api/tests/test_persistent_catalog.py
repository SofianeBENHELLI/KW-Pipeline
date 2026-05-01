import sqlite3

import pytest

from app.dependencies import build_persistent_services
from app.models.document import DocumentVersionStatus
from app.schemas.document import DocumentVersion
from app.services.catalog_store import SQLiteCatalogStore
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

    # Walk the FSM rather than jump-cutting STORED -> EXTRACTED, which the
    # lifecycle guard now refuses.
    first_services.documents.update_status(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        status=DocumentVersionStatus.EXTRACTING,
    )
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
        services.documents.upload("p.txt", "text/plain", b"x", document_id="never-existed")
    except KeyError as exc:
        assert "Document not found" in str(exc)
    else:
        raise AssertionError("Expected KeyError for missing document.")


def test_persistent_extraction_survives_restart(tmp_path):
    """Bug fix for #34: a re-built service container must surface the raw
    extraction that was saved by an earlier instance — otherwise reviewers
    can't fetch the JSON they need to validate against."""
    first = build_persistent_services(tmp_path)
    uploaded = first.documents.upload("policy.txt", "text/plain", b"first line\nsecond line")
    first.extraction_jobs.extract(document_id=uploaded.document_id, version_id=uploaded.id)

    second = build_persistent_services(tmp_path)
    raw = second.extraction_jobs.get_raw_extraction(
        document_id=uploaded.document_id, version_id=uploaded.id
    )

    assert raw.parser_name == "plain_text"
    assert raw.text == "first line\nsecond line"
    assert len(raw.source_references) == 2


def test_persistent_semantic_document_and_markdown_survive_restart(tmp_path):
    """Bug fix for #34: SemanticDocument + Markdown survive a restart so the
    reviewer's UI keeps working without re-running the pipeline."""
    first = build_persistent_services(tmp_path)
    uploaded = first.documents.upload("policy.txt", "text/plain", b"Policy title\nReview required")
    first.extraction_jobs.extract(document_id=uploaded.document_id, version_id=uploaded.id)
    first.semantic_outputs.generate(document_id=uploaded.document_id, version_id=uploaded.id)

    second = build_persistent_services(tmp_path)
    semantic = second.semantic_outputs.get(document_id=uploaded.document_id, version_id=uploaded.id)
    markdown = second.semantic_outputs.get_markdown(
        document_id=uploaded.document_id, version_id=uploaded.id
    )

    assert semantic.validation_status == "needs_review"
    assert "Policy" in markdown
    assert "## Source Lineage" in markdown


def test_sqlite_semantic_document_routes_through_loader(tmp_path):
    """ADR-008: SQLiteCatalogStore.get_semantic_document must produce a
    typed model via the schema loader, and get_semantic_document_payload
    must expose the raw dict with KeyError on miss."""
    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("policy.txt", "text/plain", b"hello")
    services.extraction_jobs.extract(document_id=uploaded.document_id, version_id=uploaded.id)
    services.semantic_outputs.generate(document_id=uploaded.document_id, version_id=uploaded.id)

    catalog = services.documents.catalog
    typed = catalog.get_semantic_document(uploaded.id)
    payload = catalog.get_semantic_document_payload(uploaded.id)

    assert typed.schema_version == "v0.1"
    assert isinstance(payload, dict)
    assert payload["schema_version"] == "v0.1"

    with pytest.raises(KeyError, match="Semantic output not found"):
        catalog.get_semantic_document_payload("nope")


def test_persistent_get_raw_extraction_raises_when_not_yet_extracted(tmp_path):
    """SQLite-specific path: uploaded but not extracted means an empty
    raw_extractions row, not a fall-through to a stale in-memory dict."""
    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("policy.txt", "text/plain", b"x")

    try:
        services.extraction_jobs.get_raw_extraction(
            document_id=uploaded.document_id, version_id=uploaded.id
        )
    except KeyError as exc:
        assert "Raw extraction not found" in str(exc)
    else:
        raise AssertionError("Expected KeyError for un-extracted version.")


def test_persistent_validate_works_after_restart(tmp_path):
    """End-to-end bug fix for #34: a reviewer can pick up a NEEDS_REVIEW
    version after a restart and validate it without 404s anywhere."""
    first = build_persistent_services(tmp_path)
    uploaded = first.documents.upload("policy.txt", "text/plain", b"line one")
    first.extraction_jobs.extract(document_id=uploaded.document_id, version_id=uploaded.id)
    first.semantic_outputs.generate(document_id=uploaded.document_id, version_id=uploaded.id)

    # Simulate the reviewer coming back the next day in a fresh process.
    second = build_persistent_services(tmp_path)
    second.documents.mark_validated(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        reviewer_note="approved post-restart",
    )
    second.semantic_outputs.record_validation(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        status="validated",
    )

    version = second.documents.get_version(uploaded.document_id, uploaded.id)
    assert version.status == DocumentVersionStatus.VALIDATED
    assert version.reviewer_note == "approved post-restart"
    semantic = second.semantic_outputs.get(document_id=uploaded.document_id, version_id=uploaded.id)
    assert semantic.validation_status == "validated"


def test_persistent_review_decision_survives_restart(tmp_path):
    first_services = build_persistent_services(tmp_path)
    uploaded = first_services.documents.upload("policy.txt", "text/plain", b"to review")
    # Drive the version through legal FSM states into NEEDS_REVIEW so we can
    # transition out of it: STORED -> EXTRACTING -> EXTRACTED -> NEEDS_REVIEW.
    first_services.documents.update_status(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        status=DocumentVersionStatus.EXTRACTING,
    )
    first_services.documents.update_status(
        document_id=uploaded.document_id,
        version_id=uploaded.id,
        status=DocumentVersionStatus.EXTRACTED,
    )
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


def test_sqlite_migration_0002_adds_review_columns_to_partial_schema(tmp_path):
    """Migration 0002 must add ``reviewer_note`` / ``reviewed_at`` to a
    database that has the base tables but was created before those columns
    were introduced.  This exercises the migration system's handling of an
    intermediate schema state (0001 applied, 0002 not yet applied)."""
    import sqlite3

    from app.services.catalog_store import SQLiteCatalogStore

    db_path = tmp_path / "partial.sqlite3"

    # Manually create the database with only migration 0001 recorded and
    # the base tables present, but WITHOUT reviewer_note / reviewed_at.
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES ('0001_initial', '2026-01-01T00:00:00+00:00');
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
        CREATE INDEX idx_document_versions_sha256 ON document_versions (sha256);
        CREATE TABLE raw_extractions (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE semantic_documents (
            document_version_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    # Instantiating the store must run migration 0002, adding the columns.
    SQLiteCatalogStore(db_path)

    inspector = sqlite3.connect(db_path)
    columns = {
        row[1] for row in inspector.execute("PRAGMA table_info(document_versions)").fetchall()
    }
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


# --- Issue #56: SQLite connections must close after every operation -------- #


def test_sqlite_connect_closes_handle_after_use(tmp_path):
    """Defect #56: `with self._connect()` previously committed but didn't close.
    The handle must be released the moment the with-block exits."""
    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    with store._connect() as connection:
        connection.execute("SELECT 1")

    # The connection is now closed; using it must raise ProgrammingError.
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_sqlite_many_operations_dont_leak_connections(tmp_path):
    """Smoke test for the leak: 250 catalog operations should not exhaust
    file descriptors. (On the buggy version, every `_connect()` left a
    handle open; the OS would close them at process exit but accumulating
    them is the issue.)"""
    services = build_persistent_services(tmp_path)
    for i in range(250):
        services.documents.upload(f"doc-{i}.txt", "text/plain", f"body {i}".encode())
    assert len(services.documents.list_documents()) == 250


# --- Issue #57: Foreign keys, busy_timeout, WAL --------------------------- #


def test_sqlite_pragma_foreign_keys_is_enabled(tmp_path):
    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    with store._connect() as connection:
        (foreign_keys,) = connection.execute("PRAGMA foreign_keys").fetchone()

    assert foreign_keys == 1


def test_sqlite_pragma_busy_timeout_is_set(tmp_path):
    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    with store._connect() as connection:
        (busy_timeout,) = connection.execute("PRAGMA busy_timeout").fetchone()

    # We set 5000 ms; assert at least the documented minimum.
    assert busy_timeout >= 5000


def test_sqlite_journal_mode_is_wal(tmp_path):
    SQLiteCatalogStore(tmp_path / "catalog.sqlite3")

    # Read journal_mode through a bare connection to avoid the store's own
    # PRAGMA defaults masking a regression.
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        (journal_mode,) = connection.execute("PRAGMA journal_mode").fetchone()

    assert journal_mode.lower() == "wal"


def test_sqlite_foreign_key_violation_is_rejected(tmp_path):
    """With foreign_keys = ON, inserting a version pointing at a non-existent
    document raises IntegrityError instead of silently orphaning the row."""
    store = SQLiteCatalogStore(tmp_path / "catalog.sqlite3")
    orphan = DocumentVersion(
        document_id="never-existed",
        version_number=1,
        filename="ghost.txt",
        content_type="text/plain",
        file_size=1,
        sha256="a" * 64,
        storage_uri="file:///tmp/ghost",
        status=DocumentVersionStatus.STORED,
    )

    with (
        pytest.raises(sqlite3.IntegrityError, match=r"FOREIGN KEY"),
        store._connect() as connection,
    ):
        store._insert_version(connection, orphan)


# --- Issue #61: find_version_by_hash must not return duplicates ---------- #


def test_third_duplicate_upload_points_at_original_not_chain(tmp_path):
    """Defect #61 (SQLite-only): the third upload of the same bytes used to
    `duplicate_of_version_id` point at the *second* upload (a duplicate
    itself), forming a chain. Must point at the original."""
    services = build_persistent_services(tmp_path)
    v1 = services.documents.upload("a.txt", "text/plain", b"shared bytes")
    v2 = services.documents.upload("b.txt", "text/plain", b"shared bytes")
    v3 = services.documents.upload("c.txt", "text/plain", b"shared bytes")

    assert v1.duplicate_of_version_id is None
    assert v2.duplicate_of_version_id == v1.id
    assert v3.duplicate_of_version_id == v1.id  # NOT v2.id


# --- Issue #62: Optimistic concurrency on status updates ----------------- #


def test_sqlite_update_version_status_rejects_illegal_predecessor(tmp_path):
    """A target whose predecessor set does not include the row's current
    status raises ``IllegalTransition`` and leaves the row alone."""
    from app.models.document import IllegalTransition

    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("policy.txt", "text/plain", b"x")  # STORED

    with pytest.raises(IllegalTransition) as excinfo:
        services.documents.catalog.update_version_status(
            document_id=uploaded.document_id,
            version_id=uploaded.id,
            status=DocumentVersionStatus.VALIDATED,
        )

    message = str(excinfo.value)
    assert "VALIDATED" in message  # target
    assert "STORED" in message  # actual
    assert "NEEDS_REVIEW" in message  # only legal predecessor of VALIDATED

    # Row still STORED — the failed UPDATE was a no-op.
    version = services.documents.get_version(uploaded.document_id, uploaded.id)
    assert version.status == DocumentVersionStatus.STORED


def test_sqlite_update_version_status_to_status_with_no_predecessors(tmp_path):
    """``DUPLICATE_DETECTED`` has no incoming FSM edges. Even if a caller
    bypasses the service layer and aims the catalog at it directly, the
    write must refuse with ``IllegalTransition`` rather than silently
    succeeding (or hitting a SQLite ``IN ()`` syntax error)."""
    from app.models.document import IllegalTransition

    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("policy.txt", "text/plain", b"x")

    with pytest.raises(IllegalTransition):
        services.documents.catalog.update_version_status(
            document_id=uploaded.document_id,
            version_id=uploaded.id,
            status=DocumentVersionStatus.DUPLICATE_DETECTED,
        )


def test_sqlite_update_version_status_missing_version_raises_keyerror(tmp_path):
    """Passing a non-existent version through the catalog still surfaces the
    KeyError ladder rather than ``IllegalTransition`` — concurrency is a
    different failure mode from "row never existed"."""
    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("policy.txt", "text/plain", b"x")

    with pytest.raises(KeyError, match="Document version not found"):
        services.documents.catalog.update_version_status(
            document_id=uploaded.document_id,
            version_id="never-existed",
            status=DocumentVersionStatus.EXTRACTING,
        )


def test_sqlite_concurrent_status_transitions_only_one_wins(tmp_path):
    """Two threads, two SQLite connections, the same legal transition: the
    optimistic ``IN (...)`` predicate guarantees exactly one writer succeeds
    and the loser raises ``IllegalTransition`` instead of silently
    overwriting the winner."""
    import threading

    from app.models.document import IllegalTransition
    from app.services.catalog_store import SQLiteCatalogStore

    # Seed the row through a service so the lifecycle is consistent, then
    # rebuild fresh stores per thread so each owns its own connection.
    services = build_persistent_services(tmp_path)
    uploaded = services.documents.upload("race.txt", "text/plain", b"contended")
    document_id, version_id = uploaded.document_id, uploaded.id

    db_path = tmp_path / "catalog.sqlite3"
    barrier = threading.Barrier(2)
    results: dict[str, BaseException | DocumentVersion] = {}

    def attempt(label: str) -> None:
        store = SQLiteCatalogStore(db_path)
        barrier.wait()
        try:
            results[label] = store.update_version_status(
                document_id=document_id,
                version_id=version_id,
                status=DocumentVersionStatus.EXTRACTING,
            )
        except BaseException as exc:  # noqa: BLE001 — we record both branches
            results[label] = exc

    threads = [
        threading.Thread(target=attempt, args=("a",)),
        threading.Thread(target=attempt, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [v for v in results.values() if isinstance(v, DocumentVersion)]
    failures = [v for v in results.values() if isinstance(v, IllegalTransition)]
    other = [v for v in results.values() if not isinstance(v, DocumentVersion | IllegalTransition)]

    assert other == [], f"Unexpected error type: {other!r}"
    assert len(successes) == 1, f"Expected exactly one winner, got {results!r}"
    assert len(failures) == 1, f"Expected exactly one IllegalTransition, got {results!r}"
    # The loser's message names both expected and actual states.
    message = str(failures[0])
    assert "EXTRACTING" in message
    assert "STORED" in message or "EXTRACTING" in message

    # Final state is the winning transition.
    final = services.documents.get_version(document_id, version_id)
    assert final.status == DocumentVersionStatus.EXTRACTING
