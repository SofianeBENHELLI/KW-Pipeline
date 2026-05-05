/**
 * TypeScript mirror of ``apps/api/app/schemas/admin_config.py::AdminConfigResponse``.
 *
 * Keep this file in sync by hand. The backend schema is small and
 * stable; it does not justify a generator round-trip for the two
 * webpack apps that don't already carry openapi-fetch (apps/widget,
 * apps/explorer). The web app could read from its generated schema
 * but consuming the same TS types as widget + explorer keeps the
 * <SettingsHub/> component portable across all three.
 */

export type ValidationMethod = "human" | "external" | "auto";
export type LogFormat = "json" | "text";

export interface UploadConfig {
  max_bytes: number;
  allowed_content_types: string[];
}

export interface CorsConfig {
  allowed_origins: string[];
  allowed_origin_regex: string;
}

export interface PersistenceConfig {
  persistent: boolean;
  data_dir: string;
}

export interface KnowledgeLayerConfig {
  enabled: boolean;
  neo4j_configured: boolean;
  neo4j_database: string;
}

export interface LLMConfig {
  configured: boolean;
  model: string;
  max_input_tokens_per_document: number;
}

export interface EmbeddingsConfig {
  configured: boolean;
  model: string;
}

export interface TaxonomyConfig {
  path: string;
  cosine_threshold: number;
}

export interface NerConfig {
  enabled: boolean;
  spacy_model: string;
}

export interface AuditConfig {
  enabled: boolean;
  db_path: string;
}

export interface IteropConfig {
  enabled: boolean;
  workflow_ref: string;
  base_url_configured: boolean;
  auth_configured: boolean;
}

export interface HitlConfig {
  default_validation_method: ValidationMethod;
  iterop: IteropConfig;
  /**
   * ADR-023 §6 corpus-wide force-auto override (EPIC-A A.8).
   * When ``true``, every version is auto-validated regardless of
   * confidence score / OCR override / SPC sampling. Surfaced here
   * so the host app can render a non-dismissible banner — a
   * load-bearing override an operator must see at a glance.
   */
  force_auto_corpus: boolean;
}

export interface LoggingConfig {
  format: LogFormat;
  level: string;
}

export interface AdminConfigResponse {
  schema_version: string;
  upload: UploadConfig;
  cors: CorsConfig;
  persistence: PersistenceConfig;
  knowledge_layer: KnowledgeLayerConfig;
  llm: LLMConfig;
  embeddings: EmbeddingsConfig;
  taxonomy: TaxonomyConfig;
  ner: NerConfig;
  audit: AuditConfig;
  hitl: HitlConfig;
  logging: LoggingConfig;
}

export type DiagnosticState = "ok" | "off" | "warn";

export interface DiagnosticTile {
  id: string;
  label: string;
  sublabel: string;
  state: DiagnosticState;
}

export interface SettingRow {
  /** Stable key — matches the Settings field name in apps/api. */
  key: string;
  /** End-user label rendered on the left of the row. */
  label: string;
  /** Short help text shown on hover (English; user-facing copy). */
  help: string;
  /** Raw value to render on the right. ``null`` → "—". */
  value: string | number | boolean | null;
  /**
   * "active" — feature is on, value displayed normally.
   * "inactive" — feature is off / not configured, row is rendered
   * greyed out so the user knows it exists but is dormant.
   * "secret-redacted" — secret-bearing field, only shows configured
   * status, never the raw secret. Always rendered active when the
   * upstream `configured` is true.
   */
  status: "active" | "inactive" | "secret-redacted";
}

export interface SettingsSection {
  id: string;
  title: string;
  rows: SettingRow[];
}
