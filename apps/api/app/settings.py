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
        default=(
            "text/plain,text/markdown,application/pdf,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
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
    cors_allowed_origin_regex: str = Field(
        default="",
        validation_alias=AliasChoices("KW_CORS_ALLOWED_ORIGIN_REGEX"),
        description=(
            "Regex matched against the request's ``Origin`` header. Empty "
            "(the default) means regex matching is disabled and only the "
            "exact ``cors_allowed_origins`` allowlist applies. Use this to "
            "cover whole tenant families without enumerating every "
            "subdomain — e.g. ``^https://.*\\.3dexperience\\.3ds\\.com$`` "
            "for any 3DEXPERIENCE on-cloud tenant. Forwarded verbatim to "
            "Starlette's ``CORSMiddleware`` ``allow_origin_regex`` "
            "parameter; an origin that matches either the CSV allowlist "
            "or this regex is accepted."
        ),
    )

    # ------------------------------------------------------------------
    # Demo / local persistence startup (issue #130)
    # ------------------------------------------------------------------
    persistent: bool = Field(
        default=False,
        validation_alias=AliasChoices("KW_PERSISTENT"),
        description=(
            "When truthy, the module-level ``app`` in :mod:`app.main` "
            "boots with the SQLite + filesystem services rooted at "
            "``data_dir`` instead of the default in-memory wiring. "
            "Programmatic ``create_app(persistent=True)`` callers are "
            "unaffected; this switch only governs the env-driven "
            "uvicorn entry point used by the local demo. Defaults to "
            "``False`` so the test suite keeps booting in-memory."
        ),
    )
    data_dir: str = Field(
        default=".kw-pipeline",
        validation_alias=AliasChoices("KW_DATA_DIR"),
        description=(
            "Filesystem root for persistent demo state. Holds the "
            "SQLite catalog and the raw-file storage tree. Reset the "
            "demo by deleting this directory."
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
    # LLM (ADR-013, amended §6 2026-05-05). Two providers are supported
    # behind the ``LLMClient`` Protocol; selection is governed by
    # ``llm_provider`` and the configured keys. Default is ``auto``:
    # Gemini wins when ``GEMINI_API_KEY`` is set, otherwise Anthropic
    # is used. Operators can pin a specific provider via
    # ``KW_LLM_PROVIDER=gemini|anthropic`` for A/B testing.
    #
    # ``ANTHROPIC_API_KEY`` and ``GEMINI_API_KEY`` are kept as legacy
    # aliases without the ``KW_`` prefix because each provider's SDK
    # ships with that exact name and operators surface them under that
    # label.
    # ------------------------------------------------------------------
    llm_provider: Literal["auto", "gemini", "anthropic"] = Field(
        default="auto",
        validation_alias=AliasChoices("KW_LLM_PROVIDER"),
        description=(
            "Active LLM provider. ``auto`` (default) prefers Gemini when "
            "``GEMINI_API_KEY`` is set and falls back to Anthropic. "
            "``gemini`` / ``anthropic`` pin the choice for A/B testing. "
            "When the pinned provider's key is missing the resolution "
            "yields no client and Phase 2 / Phase 3 stay disabled — "
            "matching the Phase 1-only behaviour the platform shipped "
            "with before this amendment."
        ),
    )
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
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("KW_GEMINI_API_KEY", "GEMINI_API_KEY"),
        description=(
            "Gemini API key. Empty disables the Gemini provider. When "
            "set with ``llm_provider=auto`` it becomes the active LLM."
        ),
    )
    gemini_model: str = Field(
        default="",
        validation_alias=AliasChoices("KW_GEMINI_MODEL"),
        description=(
            "Gemini model id override. Empty means use the SDK's default "
            "(currently ``gemini-2.5-flash``)."
        ),
    )
    entity_extractor_max_input_tokens_per_document: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "KW_ENTITY_EXTRACTOR_MAX_INPUT_TOKENS_PER_DOCUMENT",
        ),
        description=(
            "ADR-014 §3 circuit breaker. Cap on cumulative ``input_tokens`` "
            "the entity extractor may spend on a single document; once met, "
            "remaining sections are skipped and recorded as warnings. "
            "``0`` (the default) disables the breaker — matches Phase 2's "
            "original unbounded behaviour."
        ),
    )

    # ------------------------------------------------------------------
    # Embeddings (ADR-015). ``VOYAGE_API_KEY`` is kept as a legacy alias
    # because the Voyage SDK uses that exact name and operators tend to
    # surface it under that label. Phase 3 vector mode refuses to
    # construct without ``voyage_api_key``; Phase 1 + Phase 2 + the
    # existing pipeline run with it unset.
    # ------------------------------------------------------------------
    voyage_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("KW_VOYAGE_API_KEY", "VOYAGE_API_KEY"),
        description=(
            "Voyage AI API key. Empty disables Phase 3 vector embedding; "
            "Phase 1 (graph projection) and Phase 2 (entity extraction) "
            "are unaffected."
        ),
    )
    embedding_model: str = Field(
        default="voyage-3",
        validation_alias=AliasChoices(
            "KW_EMBEDDING_MODEL",
        ),
        description=(
            "Voyage embedding model id. Defaults to ``voyage-3`` per "
            "ADR-015. Operators may override (e.g. ``voyage-3-large``) "
            "without code changes; the index dimensionality is read "
            "from the configured model at construction time."
        ),
    )

    # ------------------------------------------------------------------
    # Taxonomy / ontology (ADR-017). Hybrid mode — auto-deduction is
    # the default, an operator-imposed taxonomy takes precedence when
    # configured. Editing happens through a YAML file in v1; the admin
    # HTTP route + KnowledgeForge UI are deferred with the auth story
    # (#83). Both fields are empty by default so the platform ships
    # without an opt-in.
    # ------------------------------------------------------------------
    taxonomy_path: str = Field(
        default="",
        validation_alias=AliasChoices("KW_TAXONOMY_PATH"),
        description=(
            "Filesystem path to a taxonomy YAML (ADR-017). Empty "
            "(default) means no operator-imposed taxonomy is loaded "
            "and the platform falls back to the auto-deduced topic "
            "clustering. The path is read once at startup; edits "
            "require a service restart in v1."
        ),
    )
    taxonomy_cosine_threshold: float = Field(
        default=0.55,
        validation_alias=AliasChoices("KW_TAXONOMY_COSINE_THRESHOLD"),
        description=(
            "Cosine similarity floor for the embedding-based "
            "classifier (ADR-017 §4). Chunks scoring above this "
            "threshold are assigned to their top-1 category; below, "
            "they fall back to the auto-deduced topic. Operators "
            "tune per-deployment without a code change. Range "
            "[0.0, 1.0]."
        ),
        ge=0.0,
        le=1.0,
    )

    # ------------------------------------------------------------------
    # Optional spaCy NER enricher (#190). Off by default — the spaCy
    # install lives behind the ``ner`` extra so the default wheel stays
    # slim. The enricher only loads when both this flag is truthy and
    # the optional dependency is available.
    # ------------------------------------------------------------------
    ner_enabled_raw: str = Field(
        default="",
        alias="ner_enabled",
        validation_alias=AliasChoices("KW_NER_ENABLED"),
        description=(
            "Truthy (``1``/``true``/``yes``/``on``) enables the opt-in "
            "spaCy NER enricher (person / organization). Requires the "
            "``ner`` extra to be installed and ``en_core_web_sm`` to be "
            "available; otherwise the enricher fails to construct and "
            "the API logs the misconfiguration at startup."
        ),
    )
    ner_spacy_model: str = Field(
        default="en_core_web_sm",
        validation_alias=AliasChoices("KW_NER_SPACY_MODEL"),
        description=(
            "spaCy model id loaded by the NER enricher. Defaults to "
            "``en_core_web_sm`` (small + English). Override only when "
            "the operator has installed a heavier model intentionally."
        ),
    )

    # ------------------------------------------------------------------
    # Audit event store (#26 residual). Off by default so the in-memory
    # unit suite never opens a SQLite handle. Persistent deployments
    # enable it explicitly; the documented event vocabulary then lands
    # in a queryable table alongside the structured-log lines.
    # ------------------------------------------------------------------
    audit_enabled_raw: str = Field(
        default="",
        alias="audit_enabled",
        validation_alias=AliasChoices("KW_AUDIT_ENABLED"),
        description=(
            "Truthy (``1``/``true``/``yes``/``on``) enables the SQLite "
            "audit event store. Records every dotted-name structured "
            "log event into ``audit_events`` so 'who validated doc X' "
            "is a SQL query rather than a log scrape."
        ),
    )
    audit_db_path: str = Field(
        default="",
        validation_alias=AliasChoices("KW_AUDIT_DB_PATH"),
        description=(
            "Absolute path to the audit SQLite file. Empty (default) "
            "lets the persistent-services factory derive a path from "
            "its configured data dir (``<data_dir>/audit.sqlite3``)."
        ),
    )

    # Authentication (ADR-019). Three modes selected by ``KW_AUTH_MODE``:
    # ``dev`` (default — fixed identity from ``KW_AUTH_DEV_USER``,
    # falls back to a ``"dev"`` admin user so existing tests / demos
    # work out of the box and the audit trail is attributed to a
    # recognisable actor), ``disabled`` (legacy escape hatch — anonymous
    # admin user, kept for back-compat), and ``bearer`` (HS256 JWT
    # validated against ``KW_AUTH_SECRET`` — MVP scheme; production
    # scheme is the deferred 3DEXPERIENCE context handoff).
    # ------------------------------------------------------------------
    auth_mode: str = Field(
        default="dev",
        validation_alias=AliasChoices("KW_AUTH_MODE"),
        description=(
            "Active auth mode. One of ``dev`` / ``disabled`` / "
            "``bearer`` (case-insensitive). Default ``dev`` stamps a "
            "fixed ``dev`` admin user on every request so existing "
            "tests / demos / frontend calls keep working AND every "
            "review decision lands a recognisable actor in the audit "
            "table. ``disabled`` is the legacy escape hatch (anonymous "
            "actor); ``bearer`` is the MVP signed-token mode. See "
            "ADR-019."
        ),
    )
    auth_dev_user: str = Field(
        default="",
        validation_alias=AliasChoices("KW_AUTH_DEV_USER"),
        description=(
            "Fixed user id for ``KW_AUTH_MODE=dev``. Empty (default) "
            'falls back to the literal ``"dev"`` so the mode is '
            "usable without further configuration. The role is fixed "
            "to ``admin`` in dev mode."
        ),
    )
    auth_secret: str = Field(
        default="",
        validation_alias=AliasChoices("KW_AUTH_SECRET"),
        description=(
            "HS256 secret used by ``KW_AUTH_MODE=bearer`` to verify "
            "incoming JWTs. Required when bearer mode is selected; "
            "the service refuses to construct otherwise. Empty "
            "(default) is fine for ``disabled`` / ``dev``."
        ),
    )

    # ------------------------------------------------------------------
    # HITL routing + ITEROP external review workflow (roadmap
    # 2026-05-04-hitl-and-extensions §2, future ADR-024). The adapter
    # implementation is deferred until ITEROP auth lands; these env
    # surfaces ship now so the Settings widget can render the
    # deployment posture and the operator workflow ref. The pipeline
    # routes documents per ``hitl_default_validation_method`` (``human``
    # = Orbital queue today, ``external`` = ITEROP queue once the
    # adapter wires up, ``auto`` = auto-validate without review).
    # ------------------------------------------------------------------
    hitl_default_validation_method: Literal["human", "external", "auto"] = Field(
        default="human",
        validation_alias=AliasChoices("KW_HITL_DEFAULT_VALIDATION_METHOD"),
        description=(
            "Deployment-default review routing. ``human`` keeps every "
            "doc on the in-app Orbital queue. ``external`` hands off to "
            "ITEROP via the configured workflow ref. ``auto`` skips the "
            "review gate (test-only). The Smart-HITL SPC router (Feature "
            "A) may override per-document; this is the fall-through."
        ),
    )
    iterop_enabled_raw: str = Field(
        default="",
        alias="iterop_enabled",
        validation_alias=AliasChoices("KW_ITEROP_ENABLED"),
        description=(
            "Truthy (``1``/``true``/``yes``/``on``) enables the ITEROP "
            "external-review adapter. When off, ``validation_method = "
            "external`` falls back to the in-app queue with a warning."
        ),
    )
    iterop_workflow_ref: str = Field(
        default="",
        validation_alias=AliasChoices("KW_ITEROP_WORKFLOW_REF"),
        description=(
            "ITEROP workflow identifier the adapter targets when "
            "issuing review packets (e.g. ``WF-KW-DOC-REVIEW-001``). "
            "Visible in operator surfaces — not a secret."
        ),
    )
    iterop_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("KW_ITEROP_BASE_URL"),
        description=(
            "Base URL of the ITEROP instance the adapter polls / "
            "callbacks. Empty disables external routing regardless of "
            "the kill switch."
        ),
    )
    iterop_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices("KW_ITEROP_AUTH_TOKEN"),
        description=(
            "Bearer token for ITEROP API calls. Empty disables external "
            "routing. The exact auth scheme (HMAC / OAuth / mTLS / "
            "opaque) is TBD pending the ITEROP documentation; the "
            "adapter treats this as an opaque secret."
        ),
    )

    # ------------------------------------------------------------------
    # HITL routing (ADR-023, EPIC-A, #215). Five tunable signal weights,
    # an auto-validate threshold, and a kill switch. Defaults — equal
    # weights of 0.2 each, threshold 0.85, scorer enabled — match the
    # ADR's "deployment-level config, not per-scope" decision (ADR-020
    # §3). The threshold is read but not enforced by the scorer; the
    # next-slice ``hitl_router.py`` will consume it to pick a routing
    # decision.
    # ------------------------------------------------------------------
    hitl_disable_scorer_raw: str = Field(
        default="",
        alias="hitl_disable_scorer",
        validation_alias=AliasChoices("KW_HITL_DISABLE_SCORER"),
        description=(
            "Truthy (``1``/``true``/``yes``/``on``) opts the HITL "
            "confidence scorer out of the NEEDS_REVIEW transition. "
            "Use this as the demo-safety escape hatch when the scorer "
            "code path is suspected to be flaky on a customer fixture; "
            "every other side-effect of the transition keeps running. "
            "Defaults to empty (scorer enabled) so the audit trail "
            "lights up out of the box."
        ),
    )
    hitl_weight_ocr: float = Field(
        default=0.2,
        validation_alias=AliasChoices("KW_HITL_WEIGHT_OCR"),
        description=(
            "Weight for the OCR signal in the HITL confidence score "
            "(ADR-023 §2). Note the OCR override is independent of "
            "this weight — when a version is OCR'd the score is forced "
            "to 0.0 regardless. The weight controls how much OCR-related "
            "info contributes when the override is *not* active. "
            "Negative values raise on construction."
        ),
        ge=0.0,
    )
    hitl_weight_orphan_ratio: float = Field(
        default=0.2,
        validation_alias=AliasChoices("KW_HITL_WEIGHT_ORPHAN_RATIO"),
        description="Weight for the orphan-chunk-ratio signal (ADR-023 §2).",
        ge=0.0,
    )
    hitl_weight_length_z: float = Field(
        default=0.2,
        validation_alias=AliasChoices("KW_HITL_WEIGHT_LENGTH_Z"),
        description="Weight for the section-length z-score signal (ADR-023 §2).",
        ge=0.0,
    )
    hitl_weight_topic_incoherence: float = Field(
        default=0.2,
        validation_alias=AliasChoices("KW_HITL_WEIGHT_TOPIC_INCOHERENCE"),
        description="Weight for the topic-incoherence signal (ADR-023 §2).",
        ge=0.0,
    )
    hitl_weight_citation_coverage: float = Field(
        default=0.2,
        validation_alias=AliasChoices("KW_HITL_WEIGHT_CITATION_COVERAGE"),
        description=(
            "Weight for the citation-coverage signal (ADR-023 §2), "
            "which falls back to asset-count z-score when Phase 2 is off."
        ),
        ge=0.0,
    )
    hitl_auto_validate_threshold: float = Field(
        default=0.85,
        validation_alias=AliasChoices("KW_HITL_AUTO_VALIDATE_THRESHOLD"),
        description=(
            "Auto-validate threshold — versions with confidence "
            "≥ this value are routed to the auto path by "
            "``hitl_router.py`` (slice 2). The scorer reads this only "
            "for the audit trail; enforcement lives in the router. "
            "Range [0.0, 1.0]."
        ),
        ge=0.0,
        le=1.0,
    )
    hitl_force_auto_corpus_raw: str = Field(
        default="",
        alias="hitl_force_auto_corpus",
        validation_alias=AliasChoices("KW_HITL_FORCE_AUTO_CORPUS"),
        description=(
            "ADR-023 §6 admin-mode override. Truthy "
            "(``1``/``true``/``yes``/``on``) makes the HITL router "
            "auto-route every version regardless of score, OCR flag, "
            "or SPC sampling. Used for backfill / corpus-replay runs "
            "where every version is already trusted. The router emits "
            "a loud ``hitl.force_auto_corpus_active`` warning at "
            "construction so accidental production usage is visible."
        ),
    )
    hitl_spc_sample_rate: float = Field(
        default=0.05,
        validation_alias=AliasChoices("KW_HITL_SPC_SAMPLE_RATE"),
        description=(
            "Baseline SPC (statistical process control) sampling rate "
            "for the HITL router (ADR-023 §6). Fraction of versions "
            "that *would* auto-validate but are escalated to a human "
            "as a quality probe. Default ``0.05`` keeps the auto-rate "
            "honest without flooding the review queue. The drift "
            "detector ramps this rate per-bucket; see "
            "``hitl_drift_threshold`` / ``hitl_drift_ramp_factor``. "
            "Range [0.0, 1.0]."
        ),
        ge=0.0,
        le=1.0,
    )
    hitl_drift_threshold: float = Field(
        default=0.10,
        validation_alias=AliasChoices("KW_HITL_DRIFT_THRESHOLD"),
        description=(
            "Drift ratio above which a bucket's SPC sampling rate "
            "ramps (ADR-023 §6, EPIC-A A.3 part 2). The ratio is "
            "``samples_human_after_auto / samples_auto`` per "
            "``(content_type, topic_cluster)`` bucket — when human "
            "reviewers reject auto-eligible versions at a rate above "
            "this floor, the bucket's sampling rate ramps up to "
            "catch more regressions. Range [0.0, 1.0+); typical "
            "value ``0.10`` (10% rejection rate triggers ramp)."
        ),
        ge=0.0,
    )
    hitl_drift_ramp_factor: float = Field(
        default=10.0,
        validation_alias=AliasChoices("KW_HITL_DRIFT_RAMP_FACTOR"),
        description=(
            "Multiplier applied to ``hitl_spc_sample_rate`` for "
            "drifting buckets (ADR-023 §6, EPIC-A A.3 part 2). With "
            "the default ``0.05`` baseline + ``10.0`` ramp factor, a "
            "drifting bucket samples at ``0.5`` (capped at ``1.0``). "
            "Tune up to escalate harder; tune down for less reactive "
            "behaviour. Range [0.0, ∞)."
        ),
        ge=0.0,
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
        return _truthy(self.knowledge_layer_enabled_raw)

    @property
    def ner_enabled(self) -> bool:
        """Truthy parse of the spaCy NER kill switch (#190)."""
        return _truthy(self.ner_enabled_raw)

    @property
    def audit_enabled(self) -> bool:
        """Truthy parse of the audit-store kill switch (#26 residual)."""
        return _truthy(self.audit_enabled_raw)

    @property
    def iterop_enabled(self) -> bool:
        """Truthy parse of the ITEROP adapter kill switch."""
        return _truthy(self.iterop_enabled_raw)

    @property
    def hitl_scorer_disabled(self) -> bool:
        """Truthy parse of the HITL scorer opt-out (ADR-023 §5)."""
        return _truthy(self.hitl_disable_scorer_raw)

    @property
    def hitl_force_auto_corpus(self) -> bool:
        """Truthy parse of the corpus-wide force-auto override (ADR-023 §6)."""
        return _truthy(self.hitl_force_auto_corpus_raw)

    @property
    def hitl_weights(self) -> dict[str, float]:
        """Map of canonical signal name → configured weight.

        Keys match :data:`app.services.confidence_scorer.ALL_SIGNALS`
        so the scorer can normalise the dict directly. Values are
        passed through verbatim — the scorer raises on negative or
        all-zero inputs.
        """
        return {
            "ocr": self.hitl_weight_ocr,
            "orphan_ratio": self.hitl_weight_orphan_ratio,
            "length_z": self.hitl_weight_length_z,
            "topic_incoherence": self.hitl_weight_topic_incoherence,
            "citation_coverage": self.hitl_weight_citation_coverage,
        }


def _truthy(raw: str) -> bool:
    """Same case-insensitive truthiness rule used by every kill switch."""
    return raw.strip().lower() in {"1", "true", "yes", "on"}
