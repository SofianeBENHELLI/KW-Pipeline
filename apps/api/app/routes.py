from fastapi import APIRouter, Body, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app.dependencies import PipelineServices
from app.services.extraction_job_service import ExtractionFailed


class ReviewRequest(BaseModel):
    """Optional reviewer note attached to a validate or reject decision."""

    reviewer_note: str | None = None


def build_router(services: PipelineServices) -> APIRouter:
    """Register Harvester HTTP routes against a concrete service container."""
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/documents/upload")
    async def upload_document(
        file: UploadFile = File(...),
        document_id: str | None = None,
    ):
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        try:
            return services.documents.upload(
                filename=file.filename or "untitled",
                content_type=file.content_type or "application/octet-stream",
                content=content,
                document_id=document_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        except ExtractionFailed as exc:
            raise HTTPException(status_code=422, detail=exc.reason) from exc

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

    @router.post("/documents/{document_id}/versions/{version_id}/validate")
    def validate_version(
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
    ):
        return _record_review(
            document_id=document_id,
            version_id=version_id,
            request=request,
            mark=services.documents.mark_validated,
            cached_status="validated",
        )

    @router.post("/documents/{document_id}/versions/{version_id}/reject")
    def reject_version(
        document_id: str,
        version_id: str,
        request: ReviewRequest = Body(default_factory=ReviewRequest),
    ):
        return _record_review(
            document_id=document_id,
            version_id=version_id,
            request=request,
            mark=services.documents.mark_rejected,
            cached_status="rejected",
        )

    def _record_review(
        *,
        document_id: str,
        version_id: str,
        request: ReviewRequest,
        mark,
        cached_status,
    ):
        try:
            mark(
                document_id=document_id,
                version_id=version_id,
                reviewer_note=request.reviewer_note,
            )
            return services.semantic_outputs.record_validation(
                document_id=document_id,
                version_id=version_id,
                status=cached_status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
