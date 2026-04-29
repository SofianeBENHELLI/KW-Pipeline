from fastapi import APIRouter, File, HTTPException, UploadFile

from app.dependencies import PipelineServices


def build_router(services: PipelineServices) -> APIRouter:
    """Register Harvester HTTP routes against a concrete service container."""
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/documents/upload")
    async def upload_document(file: UploadFile = File(...)):
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        return services.documents.upload(
            filename=file.filename or "untitled",
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )

    @router.get("/documents")
    def list_documents():
        return services.documents.list_documents()

    @router.get("/documents/{document_id}")
    def get_document(document_id: str):
        document = services.documents.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        return document

    @router.post("/documents/{document_id}/versions/{version_id}/extract")
    def extract_document(document_id: str, version_id: str):
        try:
            return services.extraction_jobs.extract(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/documents/{document_id}/versions/{version_id}/semantic")
    def generate_semantic_document(document_id: str, version_id: str):
        try:
            raw_extraction = services.extraction_jobs.get_raw_extraction(version_id)
            version = services.documents.get_version(document_id=document_id, version_id=version_id)
            semantic = services.semantic_extractor.extract(version=version, raw_extraction=raw_extraction)
            semantic.markdown = services.markdown_generator.render(
                version=version,
                semantic=semantic,
                raw_extraction=raw_extraction,
            )
            services.documents.mark_semantic_ready(document_id=document_id, version_id=version_id)
            return semantic
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router

