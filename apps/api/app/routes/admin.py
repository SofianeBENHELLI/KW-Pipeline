"""Admin / health endpoints.

Holds:

* ``GET /health`` — minimal liveness probe (parity with the legacy route).
* ``GET /admin/config`` — sanitized configuration snapshot consumed by
  the Knowledge Forge Settings widget (``apps/_shared/settings-hub``).
  Strips every secret (API keys, auth tokens, DB passwords) before
  returning. No auth — same posture as ``/health`` until #83 lands.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.dependencies import PipelineServices
from app.schemas.admin_config import (
    AdminConfigResponse,
    AuditConfig,
    CorsConfig,
    EmbeddingsConfig,
    HitlConfig,
    IteropConfig,
    KnowledgeLayerConfig,
    LLMConfig,
    LoggingConfig,
    NerConfig,
    PersistenceConfig,
    TaxonomyConfig,
    UploadConfig,
)
from app.schemas.document import HealthResponse
from app.settings import Settings


def _build_admin_config(settings: Settings) -> AdminConfigResponse:
    """Project a :class:`Settings` instance onto the public response shape.

    Secrets are reduced to a ``configured: bool``. Non-secret fields
    (model ids, paths, workflow refs, log level) are surfaced verbatim.
    """
    return AdminConfigResponse(
        upload=UploadConfig(
            max_bytes=settings.max_upload_bytes,
            allowed_content_types=sorted(settings.allowed_content_types),
        ),
        cors=CorsConfig(
            allowed_origins=settings.cors_allowed_origins,
            allowed_origin_regex=settings.cors_allowed_origin_regex,
        ),
        persistence=PersistenceConfig(
            persistent=settings.persistent,
            data_dir=settings.data_dir,
        ),
        knowledge_layer=KnowledgeLayerConfig(
            enabled=settings.knowledge_layer_enabled,
            neo4j_configured=bool(
                settings.neo4j_uri and settings.neo4j_user
                # neo4j_password may legitimately be empty in dev, so
                # we don't require it for ``configured`` semantics.
            ),
            neo4j_database=settings.neo4j_database,
        ),
        llm=LLMConfig(
            configured=bool(settings.anthropic_api_key),
            model=settings.anthropic_model,
            max_input_tokens_per_document=settings.entity_extractor_max_input_tokens_per_document,
        ),
        embeddings=EmbeddingsConfig(
            configured=bool(settings.voyage_api_key),
            model=settings.embedding_model,
        ),
        taxonomy=TaxonomyConfig(
            path=settings.taxonomy_path,
            cosine_threshold=settings.taxonomy_cosine_threshold,
        ),
        ner=NerConfig(
            enabled=settings.ner_enabled,
            spacy_model=settings.ner_spacy_model,
        ),
        audit=AuditConfig(
            enabled=settings.audit_enabled,
            db_path=settings.audit_db_path,
        ),
        hitl=HitlConfig(
            default_validation_method=settings.hitl_default_validation_method,
            iterop=IteropConfig(
                enabled=settings.iterop_enabled,
                workflow_ref=settings.iterop_workflow_ref,
                base_url_configured=bool(settings.iterop_base_url),
                auth_configured=bool(settings.iterop_auth_token),
            ),
        ),
        logging=LoggingConfig(
            format=settings.log_format,
            level=settings.log_level.upper(),
        ),
    )


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

    @router.get(
        "/admin/config",
        operation_id="admin_config",
        response_model=AdminConfigResponse,
    )
    def admin_config() -> AdminConfigResponse:
        # Re-read settings on every request so ``monkeypatch.setenv``
        # in tests is observed without restarting the app — same
        # posture every other call site uses.
        return _build_admin_config(Settings())

    return router
