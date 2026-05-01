"""Typed configuration surface for the Harvester API (issue #43).

Replaces the scattered ``os.environ.get`` reads that grew across
``app.main``, ``app.routes``, and ``app.dependencies`` with a single
:class:`Settings` model. Reading happens at call sites that instantiate
``Settings()`` per request — Pydantic Settings is fast enough that this
preserves the existing test ergonomics (each test does
``monkeypatch.setenv`` and expects the next request to observe it).

Prefix policy
-------------

The ``KW_`` prefix is the canonical name for every setting. The
historical unprefixed names — ``MAX_UPLOAD_BYTES``,
``ALLOWED_CONTENT_TYPES``, ``CORS_ALLOWED_ORIGINS`` — and
``ANTHROPIC_API_KEY`` are kept as :class:`pydantic.AliasChoices` so
existing deployments keep working without a config rewrite. Prefer the
``KW_*`` form in new docs and compose files.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All environment-driven configuration in one place.

    Construct with ``Settings()`` to read the current process
    environment. Construction is cheap; call sites that need to react
    to ``monkeypatch.setenv`` mid-test instantiate fresh on each
    request rather than caching a module-level instance.
    """

    model_config = SettingsConfigDict(
        env_prefix="KW_",
        # ``populate_by_name=True`` lets us pass field names directly to
        # ``Settings(...)`` in tests without going through the env layer.
        populate_by_name=True,
        # Ignore unrelated env vars — the process env on a contributor
        # machine carries plenty of noise (PATH, HOME, …) that should
        # not blow up validation.
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Upload guardrails (route-level, see ``app.routes``)
    # ------------------------------------------------------------------
    max_upload_bytes: int = Field(
        default=50 * 1024 * 1024,
        validation_alias=AliasChoices("KW_MAX_UPLOAD_BYTES", "MAX_UPLOAD_BYTES"),
        description="Hard ceiling on a single upload, in bytes. Default 50 MiB.",
    )
    allowed_content_types_csv: str = Field(
        default="text/plain",
        validation_alias=AliasChoices(
            "KW_ALLOWED_CONTENT_TYPES",
            "ALLOWED_CONTENT_TYPES",
        ),
        description=(
            "Comma-separated MIME allowlist for ``POST /documents/upload``. "
            "Empty entries are dropped."
        ),
    )

    # ------------------------------------------------------------------
    # CORS (middleware, see ``app.main``)
    # ------------------------------------------------------------------
    cors_allowed_origins_csv: str = Field(
        default="",
        validation_alias=AliasChoices(
            "KW_CORS_ALLOWED_ORIGINS",
            "CORS_ALLOWED_ORIGINS",
        ),
        description=(
            "Comma-separated origin allowlist. Empty (the default) means "
            "no cross-origin requests are accepted until an operator opts in."
        ),
    )

    # ------------------------------------------------------------------
    # Knowledge layer (ADR-012). Already prefixed historically.
    # ------------------------------------------------------------------
    knowledge_layer_enabled_raw: str = Field(
        default="",
        validation_alias=AliasChoices("KW_KNOWLEDGE_LAYER_ENABLED"),
        description=(
            "Master kill switch for the knowledge layer. Truthy values: "
            "``1``, ``true``, ``yes``, ``on`` (case-insensitive)."
        ),
    )
    neo4j_uri: str = Field(
        default="",
        validation_alias=AliasChoices("KW_NEO4J_URI"),
        description="``bolt://...`` connection string. Empty disables Neo4j wiring.",
    )
    neo4j_user: str = Field(
        default="",
        validation_alias=AliasChoices("KW_NEO4J_USER"),
        description="Neo4j auth username. Empty disables Neo4j wiring.",
    )
    neo4j_password: str = Field(
        default="",
        validation_alias=AliasChoices("KW_NEO4J_PASSWORD"),
        description="Neo4j auth password. May be the empty string in dev.",
    )
    neo4j_database: str = Field(
        default="neo4j",
        validation_alias=AliasChoices("KW_NEO4J_DATABASE"),
        description="Neo4j database name. Default ``neo4j``.",
    )

    # ------------------------------------------------------------------
    # LLM (ADR-013). ``ANTHROPIC_API_KEY`` is kept as a legacy alias
    # because the Anthropic SDK uses that exact name and many deploy
    # tools surface it under that label.
    # ------------------------------------------------------------------
    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("KW_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        description="Anthropic API key. Empty disables Phase 2 entity extraction.",
    )
    anthropic_model: str = Field(
        default="",
        # Two prefixed names: ``KW_ANTHROPIC_MODEL`` is the historical
        # env var (used in dependencies.py since Phase 2). ``KW_LLM_MODEL``
        # is the name the architecture doc has been advertising; we
        # accept both so the docs and the code line up either way.
        validation_alias=AliasChoices("KW_ANTHROPIC_MODEL", "KW_LLM_MODEL"),
        description=(
            "Claude model id override. Empty means use the SDK's default "
            "(currently ``claude-sonnet-4-5``)."
        ),
    )

    # ------------------------------------------------------------------
    # Logging (issue #42). ``json`` is the production / container shape
    # that the on-call workflow greps; ``text`` is the stdlib default
    # used for local development to keep tracebacks human-readable.
    # ------------------------------------------------------------------
    log_format: Literal["json", "text"] = Field(
        default="text",
        validation_alias=AliasChoices("KW_LOG_FORMAT"),
        description=(
            "Log line shape. ``text`` (default) uses stdlib's "
            "human-readable formatter for local dev; ``json`` emits one "
            "machine-parseable JSON object per line, suitable for "
            "container deployments where logs are scraped."
        ),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("KW_LOG_LEVEL"),
        description=(
            "Root logger level name. Standard Python logging level "
            "names (``DEBUG``/``INFO``/``WARNING``/``ERROR``/"
            "``CRITICAL``); case-insensitive."
        ),
    )

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------
    @property
    def allowed_content_types(self) -> set[str]:
        """Allowlist as a set; trims whitespace and drops empty entries."""
        return {
            entry.strip() for entry in self.allowed_content_types_csv.split(",") if entry.strip()
        }

    @property
    def cors_allowed_origins(self) -> list[str]:
        """Origin allowlist as an ordered list; trims, drops empties."""
        return [
            origin.strip() for origin in self.cors_allowed_origins_csv.split(",") if origin.strip()
        ]

    @property
    def knowledge_layer_enabled(self) -> bool:
        """Truthy parse of the kill switch.

        Matches the legacy ``_maybe_build_knowledge_layer`` truthiness:
        ``{"1", "true", "yes", "on"}`` (case-insensitive). Anything else
        — including the empty string — is False.
        """
        return self.knowledge_layer_enabled_raw.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
