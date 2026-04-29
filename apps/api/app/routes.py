from fastapi import APIRouter, File, HTTPException, Response, UploadFile

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

    @router.get("/documents/{document_id}/versions/{version_id}/extraction")
    def get_extraction(document_id: str, version_id: str):
        try:
            return services.extraction_jobs.get_raw_extraction(
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/documents/{document_id}/versions/{version_id}/semantic")
    def generate_semantic_document(document_id: str, version_id: str):
        try:
            return services.semantic_outputs.generate(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/documents/{document_id}/versions/{version_id}/semantic")
    def get_semantic_document(document_id: str, version_id: str):
        try:
            return services.semantic_outputs.get(document_id=document_id, version_id=version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/documents/{document_id}/versions/{version_id}/markdown")
    def get_markdown(document_id: str, version_id: str):
        try:
            markdown = services.semantic_outputs.get_markdown(
                document_id=document_id,
                version_id=version_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(content=markdown, media_type="text/markdown")

    return router
