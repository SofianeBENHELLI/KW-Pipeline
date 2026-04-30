from datetime import UTC

import pytest

from app.models.document import DocumentVersionStatus, IllegalTransition
from app.schemas.document import Document, DocumentVersion
from app.services.catalog_store import InMemoryCatalogStore


def _make_version(
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    sha256: str = "a" * 64,
    duplicate_of: str | None = None,
    status: DocumentVersionStatus = DocumentVersionStatus.STORED,
) -> DocumentVersion:
    return DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=1,
        filename="file.txt",
        content_type="text/plain",
        file_size=10,
        sha256=sha256,
        storage_uri=f"memory://documents/{version_id}/file.txt",
        status=status,
        duplicate_of_version_id=duplicate_of,
    )


def _make_document(version: DocumentVersion) -> Document:
    return Document.with_first_version(version)


class TestInMemoryCatalogStoreSave:
    def test_save_indexes_unique_version_by_hash(self):
        store = InMemoryCatalogStore()
        version = _make_version()

        store.save_document_with_version(_make_document(version), version)

        assert store.find_version_by_hash(version.sha256) is version
        assert store.versions[version.id] is version

    def test_save_does_not_index_duplicate_by_hash(self):
        store = InMemoryCatalogStore()
        original = _make_version(version_id="ver-1")
        store.save_document_with_version(_make_document(original), original)

        duplicate = _make_version(
            document_id="doc-2",
            version_id="ver-2",
            duplicate_of="ver-1",
            status=DocumentVersionStatus.DUPLICATE_DETECTED,
        )
        store.save_document_with_version(_make_document(duplicate), duplicate)

        # Hash should still resolve to the original, not the duplicate.
        assert store.find_version_by_hash(original.sha256) is original


class TestInMemoryCatalogStoreLookup:
    def test_get_document_returns_none_for_unknown(self):
        store = InMemoryCatalogStore()
        assert store.get_document("missing") is None

    def test_find_version_by_hash_returns_none_for_unknown(self):
        store = InMemoryCatalogStore()
        assert store.find_version_by_hash("nope") is None

    def test_get_version_raises_when_document_missing(self):
        store = InMemoryCatalogStore()

        with pytest.raises(KeyError, match="Document not found"):
            store.get_version("missing-doc", "missing-version")

    def test_get_version_raises_when_version_missing(self):
        store = InMemoryCatalogStore()
        version = _make_version()
        store.save_document_with_version(_make_document(version), version)

        with pytest.raises(KeyError, match="Document version not found"):
            store.get_version(version.document_id, "other-version-id")

    def test_list_documents_returns_all_saved(self):
        store = InMemoryCatalogStore()
        v1 = _make_version(document_id="d1", version_id="v1", sha256="a" * 64)
        v2 = _make_version(document_id="d2", version_id="v2", sha256="b" * 64)
        store.save_document_with_version(_make_document(v1), v1)
        store.save_document_with_version(_make_document(v2), v2)

        ids = sorted(d.id for d in store.list_documents())

        assert ids == ["d1", "d2"]


class TestInMemoryCatalogStoreUpdate:
    def test_update_status_changes_version_state(self):
        store = InMemoryCatalogStore()
        version = _make_version()
        store.save_document_with_version(_make_document(version), version)

        updated = store.update_version_status(
            document_id=version.document_id,
            version_id=version.id,
            status=DocumentVersionStatus.EXTRACTING,
        )

        assert updated.status == DocumentVersionStatus.EXTRACTING
        # Subsequent get reflects the new state.
        assert (
            store.get_version(version.document_id, version.id).status
            == DocumentVersionStatus.EXTRACTING
        )

    def test_update_status_propagates_missing_version(self):
        store = InMemoryCatalogStore()

        with pytest.raises(KeyError):
            store.update_version_status(
                document_id="missing",
                version_id="missing",
                status=DocumentVersionStatus.EXTRACTED,
            )

    def test_update_status_rejects_illegal_predecessor(self):
        """The InMemory store mirrors the SQLite optimistic check: a transition
        whose predecessor set doesn't include the row's current status raises
        ``IllegalTransition`` with both expected and actual states named."""
        store = InMemoryCatalogStore()
        version = _make_version()  # STORED
        store.save_document_with_version(_make_document(version), version)

        with pytest.raises(IllegalTransition) as excinfo:
            store.update_version_status(
                document_id=version.document_id,
                version_id=version.id,
                status=DocumentVersionStatus.VALIDATED,
            )

        message = str(excinfo.value)
        assert "VALIDATED" in message
        assert "STORED" in message
        # NEEDS_REVIEW is the only legal predecessor of VALIDATED.
        assert "NEEDS_REVIEW" in message
        # Catalog left untouched.
        assert (
            store.get_version(version.document_id, version.id).status
            == DocumentVersionStatus.STORED
        )

    def test_illegal_transition_subclasses_value_error(self):
        """``IllegalTransition`` must remain a ``ValueError`` so route handlers
        that translate ``ValueError -> 409`` keep working without changes."""
        assert issubclass(IllegalTransition, ValueError)


class TestInMemoryCatalogStoreAppend:
    def test_append_adds_version_and_updates_latest_pointer(self):
        store = InMemoryCatalogStore()
        v1 = _make_version(version_id="v1", sha256="a" * 64)
        store.save_document_with_version(_make_document(v1), v1)

        v2 = _make_version(
            document_id=v1.document_id,
            version_id="v2",
            sha256="b" * 64,
        )
        store.append_version_to_document(document_id=v1.document_id, version=v2)

        document = store.get_document(v1.document_id)
        assert [v.id for v in document.versions] == ["v1", "v2"]
        assert document.latest_version_id == "v2"
        assert store.versions["v2"] is v2

    def test_append_indexes_unique_version_by_hash(self):
        store = InMemoryCatalogStore()
        v1 = _make_version(version_id="v1", sha256="a" * 64)
        store.save_document_with_version(_make_document(v1), v1)

        v2 = _make_version(
            document_id=v1.document_id,
            version_id="v2",
            sha256="b" * 64,
        )
        store.append_version_to_document(document_id=v1.document_id, version=v2)

        assert store.find_version_by_hash("b" * 64) is v2

    def test_append_does_not_overwrite_hash_index_on_duplicate(self):
        store = InMemoryCatalogStore()
        v1 = _make_version(version_id="v1", sha256="a" * 64)
        store.save_document_with_version(_make_document(v1), v1)

        # Append a new version that's flagged as duplicate of v1.
        v2 = _make_version(
            document_id=v1.document_id,
            version_id="v2",
            sha256="a" * 64,
            duplicate_of="v1",
            status=DocumentVersionStatus.DUPLICATE_DETECTED,
        )
        store.append_version_to_document(document_id=v1.document_id, version=v2)

        # Hash still points at the original, not the duplicate.
        assert store.find_version_by_hash("a" * 64) is v1

    def test_append_to_unknown_document_raises_keyerror(self):
        store = InMemoryCatalogStore()
        ghost = _make_version(version_id="ghost", document_id="ghost-doc")

        with pytest.raises(KeyError, match="Document not found"):
            store.append_version_to_document(document_id="ghost-doc", version=ghost)


class TestInMemoryCatalogStoreFailure:
    def test_update_failure_sets_status_and_reason_atomically(self):
        store = InMemoryCatalogStore()
        version = _make_version()
        store.save_document_with_version(_make_document(version), version)

        updated = store.update_version_failure(
            document_id=version.document_id,
            version_id=version.id,
            reason="PlainTextParser: corrupt bytes",
        )

        assert updated.status == DocumentVersionStatus.FAILED
        assert updated.failure_reason == "PlainTextParser: corrupt bytes"

        # Subsequent get also reflects the new state.
        fetched = store.get_version(version.document_id, version.id)
        assert fetched.status == DocumentVersionStatus.FAILED
        assert fetched.failure_reason == "PlainTextParser: corrupt bytes"

    def test_update_failure_propagates_missing_document(self):
        store = InMemoryCatalogStore()

        with pytest.raises(KeyError, match="Document not found"):
            store.update_version_failure(
                document_id="missing",
                version_id="missing",
                reason="x",
            )

    def test_update_failure_propagates_missing_version(self):
        store = InMemoryCatalogStore()
        version = _make_version()
        store.save_document_with_version(_make_document(version), version)

        with pytest.raises(KeyError, match="Document version not found"):
            store.update_version_failure(
                document_id=version.document_id,
                version_id="other-version",
                reason="x",
            )


class TestInMemoryCatalogStoreReview:
    def test_update_review_writes_status_note_and_timestamp(self):
        from datetime import datetime

        store = InMemoryCatalogStore()
        version = _make_version(status=DocumentVersionStatus.NEEDS_REVIEW)
        store.save_document_with_version(_make_document(version), version)
        moment = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)

        updated = store.update_version_review(
            document_id=version.document_id,
            version_id=version.id,
            status=DocumentVersionStatus.VALIDATED,
            reviewer_note="ship it",
            reviewed_at=moment,
        )

        assert updated.status == DocumentVersionStatus.VALIDATED
        assert updated.reviewer_note == "ship it"
        assert updated.reviewed_at == moment

    def test_update_review_accepts_none_reviewer_note(self):
        from datetime import datetime

        store = InMemoryCatalogStore()
        version = _make_version(status=DocumentVersionStatus.NEEDS_REVIEW)
        store.save_document_with_version(_make_document(version), version)

        updated = store.update_version_review(
            document_id=version.document_id,
            version_id=version.id,
            status=DocumentVersionStatus.REJECTED,
            reviewer_note=None,
            reviewed_at=datetime.now(UTC),
        )

        assert updated.status == DocumentVersionStatus.REJECTED
        assert updated.reviewer_note is None
        assert updated.reviewed_at is not None

    def test_update_review_propagates_missing_document(self):
        from datetime import datetime

        store = InMemoryCatalogStore()

        with pytest.raises(KeyError, match="Document not found"):
            store.update_version_review(
                document_id="missing",
                version_id="missing",
                status=DocumentVersionStatus.VALIDATED,
                reviewer_note=None,
                reviewed_at=datetime.now(UTC),
            )


class TestInMemoryCatalogStoreArtefacts:
    """Generated artefacts (raw extraction + semantic document) round-trip
    cleanly through save/get and overwrite on repeat saves."""

    def _raw(self, version_id: str = "ver-1", text: str = "hello"):
        from app.schemas.extraction import RawExtraction

        return RawExtraction(
            document_version_id=version_id,
            parser_name="plain_text",
            parser_version="0.1",
            text=text,
        )

    def _semantic(self, version_id: str = "ver-1", title: str = "Policy"):
        from app.schemas.semantic_document import DocumentProfile, SemanticDocument

        return SemanticDocument(
            document_version_id=version_id,
            document_profile=DocumentProfile(title=title),
        )

    def test_save_and_get_raw_extraction_round_trip(self):
        store = InMemoryCatalogStore()
        raw = self._raw(text="round trip")

        store.save_raw_extraction("ver-1", raw)

        assert store.get_raw_extraction("ver-1") is raw

    def test_get_raw_extraction_raises_when_missing(self):
        store = InMemoryCatalogStore()

        with pytest.raises(KeyError, match="Raw extraction not found"):
            store.get_raw_extraction("never-extracted")

    def test_save_raw_extraction_overwrites_prior_payload(self):
        store = InMemoryCatalogStore()
        store.save_raw_extraction("ver-1", self._raw(text="first"))

        store.save_raw_extraction("ver-1", self._raw(text="second"))

        assert store.get_raw_extraction("ver-1").text == "second"

    def test_save_and_get_semantic_document_round_trip(self):
        store = InMemoryCatalogStore()
        semantic = self._semantic(title="Round Trip")

        store.save_semantic_document("ver-1", semantic)

        # Per ADR-008 the loader rebuilds the typed model on read, so we
        # compare by content rather than identity.
        loaded = store.get_semantic_document("ver-1")
        assert loaded.id == semantic.id
        assert loaded.document_profile.title == "Round Trip"
        assert loaded.schema_version == "v0.1"

    def test_get_semantic_document_payload_returns_raw_dict(self):
        store = InMemoryCatalogStore()
        semantic = self._semantic(title="Raw")

        store.save_semantic_document("ver-1", semantic)

        payload = store.get_semantic_document_payload("ver-1")

        assert isinstance(payload, dict)
        assert payload["schema_version"] == "v0.1"
        assert payload["document_profile"]["title"] == "Raw"

    def test_get_semantic_document_payload_raises_when_missing(self):
        store = InMemoryCatalogStore()

        with pytest.raises(KeyError, match="Semantic output not found"):
            store.get_semantic_document_payload("never-generated")

    def test_get_semantic_document_payload_returns_a_copy(self):
        # Mutating the returned dict must not corrupt persisted state.
        store = InMemoryCatalogStore()
        store.save_semantic_document("ver-1", self._semantic(title="Immutable"))

        payload = store.get_semantic_document_payload("ver-1")
        payload["document_profile"]["title"] = "Tampered"

        again = store.get_semantic_document_payload("ver-1")
        assert again["document_profile"]["title"] == "Immutable"

    def test_get_semantic_document_raises_when_missing(self):
        store = InMemoryCatalogStore()

        with pytest.raises(KeyError, match="Semantic output not found"):
            store.get_semantic_document("never-generated")

    def test_save_semantic_document_overwrites_prior_payload(self):
        store = InMemoryCatalogStore()
        store.save_semantic_document("ver-1", self._semantic(title="First"))

        store.save_semantic_document("ver-1", self._semantic(title="Second"))

        assert store.get_semantic_document("ver-1").document_profile.title == "Second"
