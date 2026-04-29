from app.models.document import DocumentVersionStatus
from app.schemas.extraction import RawExtraction
from app.services.document_parser import PlainTextParser
from app.services.document_service import DocumentService


class ExtractionJobService:
    """Coordinates parser execution and extraction lifecycle transitions."""

    def __init__(self, documents: DocumentService, parser: PlainTextParser):
        self.documents = documents
        self.parser = parser
        self.raw_extractions: dict[str, RawExtraction] = {}

    def extract(self, document_id: str, version_id: str) -> RawExtraction:
        """Run extraction for one stored, non-duplicate document version."""
        version = self.documents.get_version(document_id=document_id, version_id=version_id)
        if version.status == DocumentVersionStatus.DUPLICATE_DETECTED:
            raise ValueError("Duplicate versions are not extracted independently.")
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTING)
        try:
            raw_extraction = self.parser.parse(version=version, storage=self.documents.storage)
        except Exception:
            self.documents.update_status(document_id, version_id, DocumentVersionStatus.FAILED)
            raise
        self.raw_extractions[version_id] = raw_extraction
        self.documents.update_status(document_id, version_id, DocumentVersionStatus.EXTRACTED)
        return raw_extraction

    def get_raw_extraction(self, document_id: str, version_id: str) -> RawExtraction:
        """Return raw extraction output for a document version."""
        self.documents.get_version(document_id=document_id, version_id=version_id)
        raw_extraction = self.raw_extractions.get(version_id)
        if raw_extraction is None:
            raise KeyError("Raw extraction not found.")
        return raw_extraction
