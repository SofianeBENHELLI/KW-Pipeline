"""Sanitized configuration snapshot returned by ``GET /admin/config``.

Mirrors :class:`app.settings.Settings` but **strips every secret** (API
keys, auth tokens, DB passwords) — operators see ``configured: bool``
instead of the raw value. Public-but-non-secret fields (workflow refs,
base URLs, model ids, paths) are surfaced verbatim because frontends
need to display them.

The response is not paginated and not authenticated (parity with
``GET /health``). The ``schema_version`` is bumped whenever a field
moves / is removed; new optional fields don't trigger a bump.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas import APISchemaModel as BaseModel


class UploadConfig(BaseModel):
    max_bytes: int
    allowed_content_types: list[str]


class CorsConfig(BaseModel):
    allowed_origins: list[str]
    allowed_origin_regex: str


class PersistenceConfig(BaseModel):
    persistent: bool
    data_dir: str


class KnowledgeLayerConfig(BaseModel):
    enabled: bool
    neo4j_configured: bool
    neo4j_database: str


class LLMConfig(BaseModel):
    configured: bool
    model: str
    max_input_tokens_per_document: int


class EmbeddingsConfig(BaseModel):
    configured: bool
    model: str


class TaxonomyConfig(BaseModel):
    path: str
    cosine_threshold: float


class NerConfig(BaseModel):
    enabled: bool
    spacy_model: str


class AuditConfig(BaseModel):
    enabled: bool
    db_path: str


class IteropConfig(BaseModel):
    enabled: bool
    workflow_ref: str
    base_url_configured: bool
    auth_configured: bool


class HitlConfig(BaseModel):
    default_validation_method: Literal["human", "external", "auto"]
    iterop: IteropConfig
    # ADR-023 §6 corpus-wide force-auto override (EPIC-A A.8). Surfaced
    # here so the frontend renders a non-dismissible banner whenever
    # the deployment is running with every version forced through the
    # auto path — a load-bearing override an operator must see at a
    # glance. Defaults to ``False`` (the safe default — manual review
    # still gates publication unless an operator opts in).
    force_auto_corpus: bool = False


class LoggingConfig(BaseModel):
    format: Literal["json", "text"]
    level: str


class AdminConfigResponse(BaseModel):
    """Sanitized snapshot of the running deployment's configuration.

    The frontend ``apps/_shared/settings-hub`` package consumes this
    shape verbatim — keep the camelCase / snake_case posture stable.
    """

    schema_version: str = Field(default="v0.1")
    upload: UploadConfig
    cors: CorsConfig
    persistence: PersistenceConfig
    knowledge_layer: KnowledgeLayerConfig
    llm: LLMConfig
    embeddings: EmbeddingsConfig
    taxonomy: TaxonomyConfig
    ner: NerConfig
    audit: AuditConfig
    hitl: HitlConfig
    logging: LoggingConfig
