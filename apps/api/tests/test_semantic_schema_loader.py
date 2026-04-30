"""Tests for the SemanticDocument schema loader (ADR-008)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.semantic_document import DocumentProfile, SemanticDocument
from app.services.semantic_schema_loader import (
    MIGRATORS,
    UnsupportedSchemaVersion,
    load_semantic_document,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "semantic_v0_1.json"


class TestLoadV01Fixture:
    """The hand-written v0.1 fixture must round-trip through the loader and
    produce a structurally correct ``SemanticDocument``."""

    def test_fixture_loads_via_dict(self):
        with FIXTURE_PATH.open() as handle:
            payload = json.load(handle)

        doc = load_semantic_document(payload)

        assert isinstance(doc, SemanticDocument)
        assert doc.schema_version == "v0.1"
        assert doc.document_version_id == "ver-fixture-1"
        assert doc.document_profile.title == "Acceptable Use Policy"
        assert len(doc.sections) == 2
        assert doc.sections[0].heading == "Scope"
        assert len(doc.assets) == 2
        # source_backed asset retains lineage; needs_review one does not
        backed = [a for a in doc.assets if a.review_status == "source_backed"]
        assert backed and backed[0].source_reference_ids == ["ref-2"]
        assert doc.validation_status == "needs_review"
        assert doc.markdown is not None

    def test_fixture_loads_via_json_string(self):
        raw = FIXTURE_PATH.read_text()

        doc = load_semantic_document(raw)

        assert isinstance(doc, SemanticDocument)
        assert doc.schema_version == "v0.1"


class TestLoaderDispatch:
    def test_unknown_schema_version_raises(self):
        payload = {
            "document_version_id": "v",
            "schema_version": "v9.9",
            "document_profile": {"title": "T"},
        }

        with pytest.raises(UnsupportedSchemaVersion) as exc:
            load_semantic_document(payload)

        assert "v9.9" in str(exc.value)

    def test_missing_schema_version_raises_unsupported(self):
        payload = {
            "document_version_id": "v",
            "document_profile": {"title": "T"},
        }

        with pytest.raises(UnsupportedSchemaVersion):
            load_semantic_document(payload)

    def test_unsupported_schema_version_is_value_error(self):
        # Subclassing ValueError keeps the loader compatible with callers
        # that already catch ValueError around payload validation.
        assert issubclass(UnsupportedSchemaVersion, ValueError)

    def test_v0_1_migrator_is_identity(self):
        identity = MIGRATORS["v0.1"]
        sample = {"foo": "bar", "schema_version": "v0.1"}

        assert identity(sample) == sample

    def test_string_payload_is_parsed_then_loaded(self):
        # Build a minimal valid payload, serialize to JSON, and ensure the
        # loader handles the str path.
        doc = SemanticDocument(
            document_version_id="ver-1",
            document_profile=DocumentProfile(title="Round Trip"),
        )
        raw = doc.model_dump_json()

        loaded = load_semantic_document(raw)

        assert loaded.document_profile.title == "Round Trip"
        assert loaded.schema_version == "v0.1"

    def test_loader_returns_fresh_instance(self):
        # The loader is a model_validate boundary, so repeat calls on the
        # same dict produce equal but not-identical instances.
        with FIXTURE_PATH.open() as handle:
            payload = json.load(handle)

        first = load_semantic_document(payload)
        second = load_semantic_document(payload)

        assert first is not second
        assert first.model_dump() == second.model_dump()

    def test_loader_does_not_mutate_input_dict(self):
        payload = {
            "document_version_id": "ver-1",
            "schema_version": "v0.1",
            "document_profile": {"title": "T"},
        }
        snapshot = dict(payload)

        load_semantic_document(payload)

        assert payload == snapshot
