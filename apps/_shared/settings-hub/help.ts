/**
 * User-facing English help text per setting key.
 *
 * Kept here (not in the backend ``Field.description``) because the
 * Pydantic descriptions are dev-facing — they reference issues, ADRs,
 * and Pydantic alias rationales that an operator user does not care
 * about. The strings below are tuned for the Knowledge Forge Settings
 * widget tooltip: short, declarative, and free of internal jargon.
 *
 * One key per ``SettingRow.key`` rendered by ``buildSettingsSections``.
 */

export const SETTING_HELP: Record<string, string> = {
  // Upload
  "upload.max_bytes":
    "Hard ceiling on a single file upload, in bytes. Defaults to 50 MiB.",
  "upload.allowed_content_types":
    "MIME types accepted by POST /documents/upload. The default lets only plain text through; deployments add PDF / DOCX / PPTX explicitly.",

  // CORS
  "cors.allowed_origins":
    "Origins allowed to call the API from a browser. Empty means cross-origin requests are blocked entirely.",
  "cors.allowed_origin_regex":
    "Regex matched against the request's Origin header — a way to allow a whole tenant family (e.g. *.3dexperience.3ds.com) without listing each subdomain.",

  // Persistence
  "persistence.persistent":
    "When on, the API persists state to SQLite + filesystem under the data dir. When off, everything lives in memory and is lost on restart (test default).",
  "persistence.data_dir":
    "Filesystem root for persistent demo state — the SQLite catalog and the raw-file storage tree live here.",

  // Knowledge layer
  "knowledge_layer.enabled":
    "Master switch for the knowledge layer (Phase 1 graph + Phase 2 LLM + Phase 3 vector). Must be on for any of those features to do anything.",
  "knowledge_layer.neo4j":
    "Neo4j connection. When configured, the knowledge graph is persisted there; otherwise an in-memory store is used (fine for tests, not for prod).",
  "knowledge_layer.neo4j_database":
    "Neo4j database name. Defaults to 'neo4j'.",

  // LLM
  "llm.configured":
    "Anthropic API key. Required for Phase 2 entity extraction; without it the pipeline still validates documents but skips typed entities.",
  "llm.model":
    "Claude model id used for entity extraction. Empty falls back to the SDK default (currently claude-sonnet-4-5).",
  "llm.max_input_tokens_per_document":
    "Per-document budget for cumulative input tokens. Once met, remaining sections are skipped and recorded as warnings. Zero disables the breaker.",

  // Embeddings
  "embeddings.configured":
    "Voyage AI API key. Required for Phase 3 vector search and grounded chat. Without it Phase 1 + Phase 2 still work; only RAG-driven features are gated.",
  "embeddings.model":
    "Voyage embedding model id. Defaults to voyage-3 (1024-dim). Override only when the deployment provisioned a different vector index.",

  // Taxonomy
  "taxonomy.path":
    "Path to a YAML file declaring an operator-imposed taxonomy. Empty falls back to auto-deduced topic clustering.",
  "taxonomy.cosine_threshold":
    "Cosine similarity floor for the embedding-based classifier. Below this, chunks fall back to the auto-deduced topic.",

  // NER
  "ner.enabled":
    "Optional spaCy NER enricher (person / organization). Off by default; the install lives behind a 'ner' extra so the default wheel stays slim.",
  "ner.spacy_model":
    "spaCy model id loaded by the NER enricher (e.g. en_core_web_sm).",

  // Audit
  "audit.enabled":
    "When on, every dotted-name structured-log event is mirrored into a SQLite audit_events table — turns 'who validated doc X' into a SQL query rather than a log scrape.",
  "audit.db_path":
    "Absolute path to the audit SQLite file. Empty derives a path from the data dir.",

  // HITL + ITEROP
  "hitl.default_validation_method":
    "Where every doc lands by default after extraction. 'human' uses the in-app review queue; 'external' hands off to ITEROP; 'auto' skips review (test-only).",
  "hitl.iterop.enabled":
    "When on, the ITEROP adapter routes external review packets. When off, validation_method=external falls back to the in-app queue with a warning.",
  "hitl.iterop.workflow_ref":
    "ITEROP workflow id this deployment targets, e.g. WF-KW-DOC-REVIEW-001. Visible in operator surfaces — not a secret.",
  "hitl.iterop.base_url":
    "Base URL of the ITEROP instance the adapter polls and callbacks. Empty disables external routing regardless of the kill switch.",
  "hitl.iterop.auth":
    "Bearer token for ITEROP API calls. Auth scheme (HMAC / OAuth / mTLS / opaque) is TBD pending the ITEROP documentation.",

  // Logging
  "logging.format":
    "Log line shape. 'text' is human-readable for local dev; 'json' is one machine-parseable object per line, scrapeable by container log pipelines.",
  "logging.level":
    "Root logger level (DEBUG / INFO / WARNING / ERROR / CRITICAL).",
};
