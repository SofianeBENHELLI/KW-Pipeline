import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import PipelineServices, build_persistent_services, build_services
from app.routes import build_router


def _allowed_origins() -> list[str]:
    """Parse the ``CORS_ALLOWED_ORIGINS`` env var into an explicit allowlist.

    Returns an empty list when the variable is unset or blank, which means the
    API responds to no cross-origin requests until an operator opts in. The
    inline ``os.environ.get`` is intentional pending issue #43 (Pydantic
    Settings)."""
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(
    services: PipelineServices | None = None,
    *,
    persistent: bool = False,
    data_dir: str = ".kw-pipeline",
) -> FastAPI:
    """Create a Harvester API app with isolated pipeline services."""
    app = FastAPI(title="KW Pipeline Harvester API", version="0.1.0")
    if services is None:
        services = build_persistent_services(data_dir) if persistent else build_services()

    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(build_router(services))

    return app


app = create_app()
