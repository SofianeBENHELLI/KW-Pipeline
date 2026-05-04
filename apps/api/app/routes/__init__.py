"""HTTP route surface for the Harvester API.

Each route family lives in its own module under this package; the
public entry point is :func:`build_router`, kept here so existing
imports of the form ``from app.routes import build_router`` continue
to work after the audit-#222 split.

Sub-routers (in inclusion order, which matters for OpenAPI ordering
but not for routing):

- :mod:`app.routes.admin`     — ``/health`` and future operator endpoints.
- :mod:`app.routes.upload`    — single + batch document upload.
- :mod:`app.routes.lifecycle` — list / get / extract / semantic / review.
- :mod:`app.routes.knowledge` — graph / search / chat / taxonomy.

Module-level helpers (idempotency cache + per-request settings) live
in :mod:`app.routes._helpers` so each sub-router imports them without
circular dependencies.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import PipelineServices

from .admin import build_admin_router
from .knowledge import build_knowledge_router
from .lifecycle import build_lifecycle_router
from .upload import build_upload_router

__all__ = ["build_router"]


def build_router(services: PipelineServices) -> APIRouter:
    """Compose every Harvester sub-router behind one ``APIRouter``.

    Inclusion order matches the historical ``routes.py`` so the
    OpenAPI snapshot stays byte-identical: admin (health) first,
    then upload, lifecycle, knowledge.
    """
    router = APIRouter()
    router.include_router(build_admin_router(services))
    router.include_router(build_upload_router(services))
    router.include_router(build_lifecycle_router(services))
    router.include_router(build_knowledge_router(services))
    return router
