from app.schemas.extraction import RawExtraction, RawSection
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
    assert extraction.sections[0].source_reference_ids == [extraction.source_references[0].id]
    assert extraction.sections[0].heading == "Extracted Text"
    assert extraction.sections[0].id == extraction.source_references[0].section_id


def test_plain_text_parser_warns_when_no_text_is_extracted():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("blank.txt", "text/plain", b"\n\n   \n")

    extraction = PlainTextParser().parse(version=version, storage=documents.storage)

    assert extraction.source_references == []
    assert "No non-empty text lines were extracted." in extraction.warnings


def test_plain_text_parser_emits_raw_section_instances():
    documents = DocumentService(storage=InMemoryStorageService())
    version = documents.upload("policy.txt", "text/plain", b"Only line")

    extraction = PlainTextParser().parse(version=version, storage=documents.storage)

    assert all(isinstance(section, RawSection) for section in extraction.sections)
    section = extraction.sections[0]
    assert section.page_number is None
    assert section.bbox is None
    assert section.parser_metadata == {}


def test_raw_extraction_round_trips_through_json_with_typed_sections():
    raw = RawExtraction(
        document_version_id="ver-1",
        parser_name="plain_text",
        parser_version="0.1",
        text="hello",
        sections=[
            RawSection(
                id="s1",
                heading="Risks",
                text="Supplier delay",
                source_reference_ids=["r1"],
                page_number=4,
                bbox=(0.1, 0.2, 0.3, 0.4),
                parser_metadata={"confidence": "high"},
            ),
            RawSection(id="s2", text="Bare body"),
        ],
    )

    payload = raw.model_dump_json()
    restored = RawExtraction.model_validate_json(payload)

    assert restored == raw
    assert all(isinstance(section, RawSection) for section in restored.sections)
    assert restored.sections[0].bbox == (0.1, 0.2, 0.3, 0.4)
    assert restored.sections[0].parser_metadata == {"confidence": "high"}
    assert restored.sections[1].heading == "Extracted Text"
    assert restored.sections[1].source_reference_ids == []
