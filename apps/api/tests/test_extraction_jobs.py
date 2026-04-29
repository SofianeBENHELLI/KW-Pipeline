from app.models.document import DocumentVersionStatus
from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService
from app.services.extraction_job_service import ExtractionJobService
from app.services.storage_service import InMemoryStorageService


def test_extraction_generates_raw_json_and_updates_status():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("note.txt", "text/plain", b"First line\nSecond line")
    jobs = ExtractionJobService(documents=documents, parser=PlainTextParser())

    extraction = jobs.extract(document_id=version.document_id, version_id=version.id)

    assert extraction.parser_name == "plain_text"
    assert extraction.text == "First line\nSecond line"
    assert len(extraction.source_references) == 2
    assert documents.get_version(version.document_id, version.id).status == DocumentVersionStatus.EXTRACTED
