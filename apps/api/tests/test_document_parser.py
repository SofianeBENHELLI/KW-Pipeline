from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService
from app.services.storage_service import InMemoryStorageService


def test_plain_text_parser_creates_line_level_source_references():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("policy.txt", "text/plain", b"Title\n\nRule one")

    extraction = PlainTextParser().parse(version=version, storage=documents.storage)

    assert extraction.parser_name == "plain_text"
    assert extraction.parser_version == "0.1"
    assert [ref.line_start for ref in extraction.source_references] == [1, 3]
    assert extraction.sections[0]["source_reference_ids"] == [extraction.source_references[0].id]


def test_plain_text_parser_warns_when_no_text_is_extracted():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("blank.txt", "text/plain", b"\n\n   \n")

    extraction = PlainTextParser().parse(version=version, storage=documents.storage)

    assert extraction.source_references == []
    assert "No non-empty text lines were extracted." in extraction.warnings

