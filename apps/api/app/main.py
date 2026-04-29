from fastapi import FastAPI

from app.dependencies import PipelineServices, build_persistent_services, build_services
from app.routes import build_router


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
    app.include_router(build_router(services))

    return app


app = create_app()
