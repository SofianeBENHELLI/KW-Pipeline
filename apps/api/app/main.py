from fastapi import FastAPI, File, HTTPException, UploadFile

from app.services.document_service import DocumentService
from app.services.document_parser import PlainTextParser
from app.services.extraction_job_service import ExtractionJobService
from app.services.markdown_generator import MarkdownGenerator
from app.services.semantic_extractor import SemanticExtractor
from app.services.storage_service import InMemoryStorageService

app = FastAPI(title="KW Pipeline Harvester API", version="0.1.0")

storage = InMemoryStorageService()
documents = DocumentService(storage=storage)
parser = PlainTextParser()
extractor = ExtractionJobService(documents=documents, parser=parser)
semantic_extractor = SemanticExtractor()
markdown_generator = MarkdownGenerator()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    return documents.upload(
        filename=file.filename or "untitled",
        content_type=file.content_type or "application/octet-stream",
        content=content,
    )


@app.get("/documents")
def list_documents():
    return documents.list_documents()


@app.get("/documents/{document_id}")
def get_document(document_id: str):
    document = documents.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@app.post("/documents/{document_id}/versions/{version_id}/extract")
def extract_document(document_id: str, version_id: str):
    try:
        return extractor.extract(document_id=document_id, version_id=version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/documents/{document_id}/versions/{version_id}/semantic")
def generate_semantic_document(document_id: str, version_id: str):
    try:
        raw_extraction = extractor.get_raw_extraction(version_id)
        version = documents.get_version(document_id=document_id, version_id=version_id)
        semantic = semantic_extractor.extract(version=version, raw_extraction=raw_extraction)
        markdown = markdown_generator.render(version=version, semantic=semantic, raw_extraction=raw_extraction)
        semantic.markdown = markdown
        documents.mark_semantic_ready(document_id=document_id, version_id=version_id)
        return semantic
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
