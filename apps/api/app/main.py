from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import PipelineServices, build_persistent_services, build_services
from app.errors import install_error_handlers
from app.logging_config import configure_logging
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
    app = FastAPI(
        title="KW Pipeline Harvester API",
        version="0.1.0",
        description=(
            "Auditable document-intelligence pipeline. Endpoints cover "
            "upload, hashing, duplicate detection, parsing, semantic-JSON "
            "generation, reviewer validate/reject, and an optional "
            "knowledge-graph projection (ADR-012). Every claim and edge "
            "carries provenance via `source_reference_id`. "
            "See `docs/architecture/document_intelligence_mvp.md` for the "
            "ingestion contract and `docs/architecture/api_contract.md` "
            "for the public response shapes."
        ),
        contact={
            "name": "KW Pipeline",
            "url": "https://github.com/SofianeBENHELLI/KW-Pipeline",
        },
        license_info={
            "name": "Proprietary — all rights reserved",
            "url": "https://github.com/SofianeBENHELLI/KW-Pipeline/blob/main/LICENSE",
        },
    )
    if services is None:
        services = build_persistent_services(data_dir) if persistent else build_services()

    # Install the structured-logging handler once per app instance
    # (issue #42). ``configure_logging`` is idempotent — replacing the
    # root handler — so test suites that build many ``create_app``
    # instances in one process don't produce duplicate log lines.
    configure_logging(services.settings)

    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Issue #120 — wrap raised HTTPExceptions into the public error
    # envelope defined in ``app.errors``. The legacy ``detail`` field is
    # preserved alongside ``error.{code,message,status}`` so existing
    # clients and tests don't break.
    install_error_handlers(app)

    app.include_router(build_router(services))

    return app


def _build_app() -> FastAPI:
    """Pick in-memory vs persistent wiring based on the env-driven settings.

    Used only for the module-level ``app`` symbol that uvicorn imports
    via ``app.main:app`` (issue #130 — demo MVP startup path). The
    programmatic ``create_app(persistent=True)`` route the test suite
    and ``docs/architecture/persistence.md`` exercise is unchanged: this
    helper exists exclusively so a presenter can flip ``KW_PERSISTENT=true``
    in the environment instead of editing Python.
    """
    settings = Settings()
    return create_app(
        persistent=settings.persistent,
        data_dir=settings.data_dir,
    )


app = _build_app()
