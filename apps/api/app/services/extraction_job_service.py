import logging

from app.models.document import DocumentVersionStatus
from app.schemas.extraction import RawExtraction
from app.services.document_parser import ParserRegistry
from app.services.document_service import DocumentService

log = logging.getLogger(__name__)


class ExtractionFailed(Exception):
    """Raised when a parser fails. Carries the persisted, human-readable reason
    so HTTP routes (and other callers) can surface the same string the catalog
    stored on the version."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ExtractionJobService:
    """Coordinates parser execution and extraction lifecycle transitions.

    Raw extraction JSON is persisted via the catalog store, so re-fetching
    a previously extracted version after a process restart returns the
    same payload without re-running the parser.

    Parser selection is delegated to a ``ParserRegistry`` keyed on
    ``DocumentVersion.content_type``. The registry decouples the job
    service from any specific parser implementation and lets a single
    deployment serve multiple content types. The legacy ``parser=`` shim
    introduced by #39 is dropped here — call sites must pass
    ``parsers=ParserRegistry([...])``.
    """

    def __init__(self, *, documents: DocumentService, parsers: ParserRegistry):
        self.documents = documents
        self.parsers = parsers

    def extract(self, document_id: str, version_id: str) -> RawExtraction:
        """Run extraction for one stored, non-duplicate document version."""
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        if version.status == DocumentVersionStatus.DUPLICATE_DETECTED:
            raise ValueError("Duplicate versions are not extracted independently.")
        log.info(
            "extraction.started",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "content_type": version.content_type,
                "bytes_in": version.file_size,
            },
        )
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTING)
        try:
            parser = self.parsers.for_content_type(version.content_type)
        except KeyError as exc:
            reason = (
                str(exc.args[0])
                if exc.args
                else f"No parser for content_type: {version.content_type}"
            )
            self.documents.mark_failed(document_id, version_id, reason)
            log.warning(
                "extraction.failed",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "parser_name": None,
                    "failure_reason": reason,
                },
            )
            raise ExtractionFailed(reason) from exc
        parser_name = type(parser).__name__
        try:
            raw_extraction = parser.parse(version=version, storage=self.documents.storage)
        except Exception as exc:
            reason = f"{parser_name}: {exc}"
            self.documents.mark_failed(document_id, version_id, reason)
            log.warning(
                "extraction.failed",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "parser_name": parser_name,
                    "failure_reason": reason,
                },
            )
            raise ExtractionFailed(reason) from exc
        if not raw_extraction.source_references:
            reason = f"{parser_name}: No extractable content"
            self.documents.mark_failed(document_id, version_id, reason)
            log.warning(
                "extraction.failed",
                extra={
                    "document_id": document_id,
                    "version_id": version_id,
                    "parser_name": parser_name,
                    "failure_reason": reason,
                },
            )
            raise ExtractionFailed(reason)
        self.documents.catalog.save_raw_extraction(version_id, raw_extraction)
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTED)
        log.info(
            "extraction.succeeded",
            extra={
                "document_id": document_id,
                "version_id": version_id,
                "parser_name": parser_name,
                "bytes_in": version.file_size,
                "sections_out": len(raw_extraction.source_references),
            },
        )
        return raw_extraction

    def get_raw_extraction(self, document_id: str, version_id: str) -> RawExtraction:
        """Return raw extraction output for a document version."""
        self.documents.get_version(document_id=document_id, version_id=version_id)
        return self.documents.catalog.get_raw_extraction(version_id)
