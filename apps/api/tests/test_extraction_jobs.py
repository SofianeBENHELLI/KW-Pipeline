import pytest

from app.models.document import DocumentVersionStatus
from app.schemas.document import Document, DocumentVersion
from app.services.document_parser import ParserRegistry, PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionFailed, ExtractionJobService
from app.services.storage_service import InMemoryStorageService


class AlwaysFailingParser:
    """Parser stub that raises on every parse — used to test the FAILED path."""

    name = "always_failing"
    version = "test"
    supported_content_types = frozenset({"text/plain"})

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
    jobs = ExtractionJobService(documents=documents, parser=AlwaysFailingParser())

    with pytest.raises(ExtractionFailed) as excinfo:
        jobs.extract(document_id=version.document_id, version_id=version.id)

    # The reason carried on the exception is the same string persisted on the
    # version, prefixed with the parser class name.
    assert excinfo.value.reason == "AlwaysFailingParser: simulated parser failure"
    # Original parser exception is preserved as __cause__.
    assert isinstance(excinfo.value.__cause__, RuntimeError)

    failed = documents.get_version(version.document_id, version.id)
    assert failed.status == DocumentVersionStatus.FAILED
    assert failed.failure_reason == "AlwaysFailingParser: simulated parser failure"

    # No raw extraction was cached for the failing run.
    with pytest.raises(KeyError, match="Raw extraction not found"):
        jobs.get_raw_extraction(document_id=version.document_id, version_id=version.id)


def test_successful_extraction_does_not_set_failure_reason():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("note.txt", "text/plain", b"line one\nline two")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    jobs.extract(document_id=version.document_id, version_id=version.id)

    assert documents.get_version(version.document_id, version.id).failure_reason is None


def test_extraction_refuses_duplicate_versions():
    documents = DocumentService(storage=InMemoryStorageService())
    documents.upload("a.txt", "text/plain", b"shared")
    duplicate = documents.upload("b.txt", "text/plain", b"shared")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    assert duplicate.status == DocumentVersionStatus.DUPLICATE_DETECTED

    with pytest.raises(ValueError, match="Duplicate versions"):
        jobs.extract(document_id=duplicate.document_id, version_id=duplicate.id)


def test_get_raw_extraction_raises_when_version_was_uploaded_but_not_extracted():
    documents = DocumentService(storage=InMemoryStorageService())
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())
    version = documents.upload("policy.txt", "text/plain", b"never extracted")

    with pytest.raises(KeyError, match="Raw extraction not found"):
        jobs.get_raw_extraction(
            document_id=version.document_id,
            version_id=version.id,
        )


def test_get_raw_extraction_raises_for_unknown_document():
    documents = DocumentService(storage=InMemoryStorageService())
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    with pytest.raises(KeyError):
        jobs.get_raw_extraction(
            document_id="missing-document-id",
            version_id="missing-version-id",
        )


def test_extraction_dispatches_through_parser_registry():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("note.txt", "text/plain", b"hello")
    registry = ParserRegistry([PlainTextParser()])
    jobs = ExtractionJobService(documents=documents, parsers=registry)

    extraction = jobs.extract(document_id=version.document_id, version_id=version.id)

    assert extraction.parser_name == "plain_text"
    assert (
        documents.get_version(version.document_id, version.id).status
        == DocumentVersionStatus.EXTRACTED
    )


def test_extraction_marks_version_failed_for_unsupported_content_type():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("scan.pdf", "application/pdf", b"%PDF-1.7 fake")
    # Registry knows only about text/plain, so application/pdf is unsupported.
    jobs = ExtractionJobService(documents=documents, parsers=ParserRegistry([PlainTextParser()]))

    with pytest.raises(ExtractionFailed) as excinfo:
        jobs.extract(document_id=version.document_id, version_id=version.id)

    assert excinfo.value.reason == "No parser for content_type: application/pdf"
    failed = documents.get_version(version.document_id, version.id)
    assert failed.status == DocumentVersionStatus.FAILED
    assert failed.failure_reason == "No parser for content_type: application/pdf"


def test_extraction_checks_lifecycle_before_parser_registry_failure():
    documents = DocumentService(storage=InMemoryStorageService())
    version = DocumentVersion(
        id="ver-reviewed-pdf",
        document_id="doc-reviewed-pdf",
        version_number=1,
        filename="reviewed.pdf",
        content_type="application/pdf",
        file_size=12,
        sha256="a" * 64,
        storage_uri="memory://documents/ver-reviewed-pdf/reviewed.pdf",
        status=DocumentVersionStatus.VALIDATED,
    )
    documents.catalog.save_document_with_version(
        document=Document.with_first_version(version),
        version=version,
    )
    jobs = ExtractionJobService(documents=documents, parsers=ParserRegistry([PlainTextParser()]))

    with pytest.raises(ValueError, match="Cannot transition from VALIDATED to EXTRACTING"):
        jobs.extract(document_id=version.document_id, version_id=version.id)

    current = documents.get_version(version.document_id, version.id)
    assert current.status == DocumentVersionStatus.VALIDATED
    assert current.failure_reason is None


def test_extraction_job_service_requires_parsers_or_parser():
    documents = DocumentService(storage=InMemoryStorageService())
    with pytest.raises(TypeError, match="parsers=` or `parser="):
        ExtractionJobService(documents=documents)
