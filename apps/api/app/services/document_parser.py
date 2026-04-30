from typing import Protocol, runtime_checkable

from app.schemas.document import DocumentVersion
from app.schemas.extraction import RawExtraction, RawSection, SourceReference
from app.services.storage_service import StorageService


@runtime_checkable
class Parser(Protocol):
    """Pluggable parser contract for document extraction.

    Concrete parsers (plain text today; PDF/DOCX/OCR later via #45/#46/#47)
    declare which content types they accept and turn stored bytes into a
    ``RawExtraction`` payload. The ``ParserRegistry`` dispatches versions to
    the right parser based on ``DocumentVersion.content_type``.
    """

    name: str
    version: str
    supported_content_types: frozenset[str]

    def parse(self, version: DocumentVersion, storage: StorageService) -> RawExtraction:
        """Read stored bytes for ``version`` and return inspectable extraction JSON."""
        ...


class ParserRegistry:
    """Resolve a ``Parser`` for a given content type.

    The registry is content-type indexed; each parser is registered for every
    type it advertises in ``supported_content_types``. The first parser
    registered for a type wins, so order in the constructor list matters when
    parsers overlap.
    """

    def __init__(self, parsers: list[Parser]):
        self._by_content_type: dict[str, Parser] = {}
        for parser in parsers:
            for content_type in parser.supported_content_types:
                # First-registered wins; later overlaps are ignored.
                self._by_content_type.setdefault(content_type, parser)

    def for_content_type(self, content_type: str) -> Parser:
        """Return the parser handling ``content_type`` or raise ``KeyError``."""
        try:
            return self._by_content_type[content_type]
        except KeyError as exc:
            raise KeyError(f"No parser for content_type: {content_type}") from exc


class PlainTextParser:
    """Deterministic parser used until Docling integration is introduced.

    It treats each non-empty text line as a reviewable source reference while
    preserving original line numbers for traceability. Registered for
    ``text/plain`` via ``supported_content_types`` so the registry picks it up.
    """

    name = "plain_text"
    version = "0.1"
    supported_content_types: frozenset[str] = frozenset({"text/plain"})

    def parse(self, version: DocumentVersion, storage: StorageService) -> RawExtraction:
        """Parse stored bytes into inspectable raw extraction JSON."""
        content = storage.get(version.storage_uri)
        text = content.decode("utf-8", errors="replace")
        source_references = [
            SourceReference(
                document_version_id=version.id,
                section_id=f"line-{index}",
                line_start=index,
                line_end=index,
                snippet=line.strip()[:240],
            )
            for index, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]
        sections = [
            RawSection(
                id=ref.section_id,
                heading="Extracted Text",
                text=ref.snippet,
                source_reference_ids=[ref.id],
            )
            for ref in source_references
        ]
        warnings = []
        if not source_references:
            warnings.append("No non-empty text lines were extracted.")
        return RawExtraction(
            document_version_id=version.id,
            parser_name=self.name,
            parser_version=self.version,
            text=text,
            sections=sections,
            source_references=source_references,
            warnings=warnings,
        )
