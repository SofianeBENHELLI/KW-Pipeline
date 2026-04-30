from app.models.document import DocumentVersionStatus
from app.schemas.extraction import RawExtraction
from app.services.document_parser import Parser, ParserRegistry
from app.services.document_service import DocumentService


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

    Parser dispatch is handled by ``ParserRegistry``; the right concrete parser
    is picked at extract time using ``DocumentVersion.content_type``. The legacy
    ``parser=`` keyword is retained so existing wiring (``dependencies.py``)
    and tests that monkey-patch ``services.extraction_jobs.parser`` keep working
    while the multi-parser ecosystem (#45/#46/#47) is being built out. Final
    registry wiring in ``dependencies.py`` is owned by the supervisor and is
    pending follow-up.
    """

    def __init__(
        self,
        documents: DocumentService,
        parsers: ParserRegistry | None = None,
        *,
        parser: Parser | None = None,
    ):
        if parsers is None and parser is None:
            raise TypeError("ExtractionJobService requires `parsers=` or `parser=`.")
        if parsers is None:
            # Legacy single-parser path: wrap the lone parser in a registry
            # so extract() can still dispatch by content_type.
            parsers = ParserRegistry([parser])
        self.documents = documents
        self.parsers = parsers
        # Kept for backward compatibility with callers that read or override
        # ``services.extraction_jobs.parser`` directly. New code should
        # consult ``self.parsers``.
        self.parser = parser

    def extract(self, document_id: str, version_id: str) -> RawExtraction:
        """Run extraction for one stored, non-duplicate document version."""
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        if version.status == DocumentVersionStatus.DUPLICATE_DETECTED:
            raise ValueError("Duplicate versions are not extracted independently.")
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
            raise ExtractionFailed(reason) from exc
        try:
            raw_extraction = parser.parse(version=version, storage=self.documents.storage)
        except Exception as exc:
            reason = f"{type(parser).__name__}: {exc}"
            self.documents.mark_failed(document_id, version_id, reason)
            raise ExtractionFailed(reason) from exc
        self.documents.catalog.save_raw_extraction(version_id, raw_extraction)
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTED)
        return raw_extraction

    def get_raw_extraction(self, document_id: str, version_id: str) -> RawExtraction:
        """Return raw extraction output for a document version."""
        self.documents.get_version(document_id=document_id, version_id=version_id)
        return self.documents.catalog.get_raw_extraction(version_id)
