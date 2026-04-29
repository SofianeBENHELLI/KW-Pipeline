from app.models.document import DocumentVersionStatus
from app.schemas.extraction import RawExtraction
from app.services.document_parser import PlainTextParser
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
    """

    def __init__(self, documents: DocumentService, parser: PlainTextParser):
        self.documents = documents
        self.parser = parser

    def extract(self, document_id: str, version_id: str) -> RawExtraction:
        """Run extraction for one stored, non-duplicate document version."""
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        if version.status == DocumentVersionStatus.DUPLICATE_DETECTED:
            raise ValueError("Duplicate versions are not extracted independently.")
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTING)
        try:
            raw_extraction = self.parser.parse(version=version, storage=self.documents.storage)
        except Exception as exc:
            reason = f"{type(self.parser).__name__}: {exc}"
            self.documents.mark_failed(document_id, version_id, reason)
            raise ExtractionFailed(reason) from exc
        self.documents.catalog.save_raw_extraction(version_id, raw_extraction)
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTED)
        return raw_extraction

    def get_raw_extraction(self, document_id: str, version_id: str) -> RawExtraction:
        """Return raw extraction output for a document version."""
        self.documents.get_version(document_id=document_id, version_id=version_id)
        return self.documents.catalog.get_raw_extraction(version_id)
