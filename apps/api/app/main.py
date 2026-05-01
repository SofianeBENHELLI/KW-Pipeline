from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import PipelineServices, build_persistent_services, build_services
from app.routes import build_router
from app.settings import Settings


def _allowed_origins() -> list[str]:
    """Parse the CORS allowlist from the typed settings model.

    Returns an empty list when the variable is unset or blank, which means the
    API responds to no cross-origin requests until an operator opts in.
    Reads are routed through :class:`app.settings.Settings` (issue #43);
    ``KW_CORS_ALLOWED_ORIGINS`` is the canonical name and the legacy
    ``CORS_ALLOWED_ORIGINS`` keeps working as a Pydantic alias.
    """
    return Settings().cors_allowed_origins


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
