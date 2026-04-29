import pytest

from app.models.document import DocumentVersionStatus
from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.storage_service import InMemoryStorageService


class _AlwaysFailingParser:
    """Parser stub that raises on every parse — used to test the FAILED path."""

    def parse(self, version, storage):
        raise RuntimeError("simulated parser failure")


def test_extraction_generates_raw_json_and_updates_status():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("note.txt", "text/plain", b"First line\nSecond line")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    extraction = jobs.extract(document_id=version.document_id, version_id=version.id)

    assert extraction.parser_name == "plain_text"
    assert extraction.text == "First line\nSecond line"
    assert len(extraction.source_references) == 2
    assert (
        documents.get_version(version.document_id, version.id).status
        == DocumentVersionStatus.EXTRACTED
    )


def test_extraction_marks_version_failed_when_parser_raises():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("broken.txt", "text/plain", b"anything")
    jobs = ExtractionJobService(documents=documents, parser=_AlwaysFailingParser())

    with pytest.raises(RuntimeError, match="simulated parser failure"):
        jobs.extract(document_id=version.document_id, version_id=version.id)

    # Status flipped to FAILED before the exception propagated.
    assert (
        documents.get_version(version.document_id, version.id).status
        == DocumentVersionStatus.FAILED
    )
    # No raw extraction was cached for the failing run.
    with pytest.raises(KeyError):
        jobs.get_raw_extraction(version.id)


def test_extraction_refuses_duplicate_versions():
    documents = DocumentService(storage=InMemoryStorageService())
    documents.upload("a.txt", "text/plain", b"shared")
    duplicate = documents.upload("b.txt", "text/plain", b"shared")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED

    with pytest.raises(ValueError, match="Duplicate versions"):
        jobs.extract(document_id=duplicate.document_id, version_id=duplicate.id)


def test_get_raw_extraction_raises_for_unknown_version():
    documents = DocumentService(storage=InMemoryStorageService())
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    with pytest.raises(KeyError, match="Raw extraction not found"):
        jobs.get_raw_extraction("never-extracted-version-id")

