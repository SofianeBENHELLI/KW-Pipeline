"""Admin / health endpoints.

Today this file holds only ``GET /health``; future operator-facing
endpoints (readiness probes, metrics scrape, reconciliation triggers)
land here as they are added.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import PipelineServices
from app.schemas.document import HealthResponse


def build_admin_router(services: PipelineServices) -> APIRouter:  # noqa: ARG001 — services unused today, but every sub-router takes it for symmetry
    """Register admin / health routes.

    ``services`` is accepted but unused at present so the call shape
    matches the other ``build_*_router`` factories — that uniformity
    is what lets ``app.routes.__init__`` compose them in a loop.
    """
    router = APIRouter()

    @router.get("/health", operation_id="health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return router
