/**
 * Public surface of the settings-hub package.
 *
 * Data-only by design — every host app (apps/widget, apps/web,
 * apps/explorer) imports the types + the ``buildSettingsSections``
 * + ``buildDiagnosticTiles`` projections + ``fetchAdminConfig``,
 * then renders them with its own React component because each
 * surface wants to feel native (3DDashboard chrome / Vite app /
 * Explorer rail).
 */

export { fetchAdminConfig, ApiError } from "./fetchAdminConfig";
export {
  buildSettingsSections,
  buildDiagnosticTiles,
} from "./buildSections";
export { SETTING_HELP } from "./help";
export type {
  AdminConfigResponse,
  AuditConfig,
  CorsConfig,
  DiagnosticState,
  DiagnosticTile,
  EmbeddingsConfig,
  HitlConfig,
  IteropConfig,
  KnowledgeLayerConfig,
  LLMConfig,
  LoggingConfig,
  NerConfig,
  PersistenceConfig,
  SettingRow,
  SettingsSection,
  TaxonomyConfig,
  UploadConfig,
  ValidationMethod,
} from "./types";
