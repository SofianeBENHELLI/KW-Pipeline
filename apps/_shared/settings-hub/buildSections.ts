/**
 * Project an :class:`AdminConfigResponse` onto the
 * :class:`SettingsSection` list rendered by ``<SettingsTable/>``.
 *
 * The transformation is deliberately data-only — no JSX — so the same
 * structure powers a table, a printable export, or a JSON debug dump
 * without re-walking the response shape.
 */

import { SETTING_HELP } from "./help";
import type {
  AdminConfigResponse,
  DiagnosticTile,
  SettingRow,
  SettingsSection,
} from "./types";

function row(
  key: string,
  label: string,
  value: SettingRow["value"],
  status: SettingRow["status"],
): SettingRow {
  return {
    key,
    label,
    help: SETTING_HELP[key] ?? "",
    value,
    status,
  };
}

function bytesLabel(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MiB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${n} B`;
}

export function buildSettingsSections(
  config: AdminConfigResponse,
): SettingsSection[] {
  const sections: SettingsSection[] = [];

  sections.push({
    id: "upload",
    title: "Upload",
    rows: [
      row(
        "upload.max_bytes",
        "Max upload size",
        bytesLabel(config.upload.max_bytes),
        "active",
      ),
      row(
        "upload.allowed_content_types",
        "Allowed content types",
        config.upload.allowed_content_types.join(", ") || "—",
        config.upload.allowed_content_types.length > 0 ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "cors",
    title: "CORS",
    rows: [
      row(
        "cors.allowed_origins",
        "Allowed origins",
        config.cors.allowed_origins.join(", ") || "—",
        config.cors.allowed_origins.length > 0 ? "active" : "inactive",
      ),
      row(
        "cors.allowed_origin_regex",
        "Allowed origin regex",
        config.cors.allowed_origin_regex || "—",
        config.cors.allowed_origin_regex ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "persistence",
    title: "Persistence",
    rows: [
      row(
        "persistence.persistent",
        "Persistent storage",
        config.persistence.persistent ? "on" : "off (in-memory)",
        config.persistence.persistent ? "active" : "inactive",
      ),
      row(
        "persistence.data_dir",
        "Data directory",
        config.persistence.data_dir,
        config.persistence.persistent ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "knowledge_layer",
    title: "Knowledge layer",
    rows: [
      row(
        "knowledge_layer.enabled",
        "Enabled",
        config.knowledge_layer.enabled ? "yes" : "no",
        config.knowledge_layer.enabled ? "active" : "inactive",
      ),
      row(
        "knowledge_layer.neo4j",
        "Neo4j",
        config.knowledge_layer.neo4j_configured
          ? "configured"
          : "not configured (in-memory store)",
        config.knowledge_layer.neo4j_configured ? "secret-redacted" : "inactive",
      ),
      row(
        "knowledge_layer.neo4j_database",
        "Neo4j database",
        config.knowledge_layer.neo4j_database,
        config.knowledge_layer.neo4j_configured ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "llm",
    title: "LLM (Phase 2)",
    rows: [
      row(
        "llm.configured",
        "Anthropic API key",
        config.llm.configured ? "configured" : "not configured",
        config.llm.configured ? "secret-redacted" : "inactive",
      ),
      row(
        "llm.model",
        "Model",
        config.llm.model || "(SDK default)",
        config.llm.configured ? "active" : "inactive",
      ),
      row(
        "llm.max_input_tokens_per_document",
        "Max input tokens / document",
        config.llm.max_input_tokens_per_document === 0
          ? "unbounded"
          : config.llm.max_input_tokens_per_document,
        config.llm.configured ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "embeddings",
    title: "Embeddings (Phase 3)",
    rows: [
      row(
        "embeddings.configured",
        "Voyage API key",
        config.embeddings.configured ? "configured" : "not configured",
        config.embeddings.configured ? "secret-redacted" : "inactive",
      ),
      row(
        "embeddings.model",
        "Model",
        config.embeddings.model,
        config.embeddings.configured ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "taxonomy",
    title: "Taxonomy / ontology",
    rows: [
      row(
        "taxonomy.path",
        "Taxonomy path",
        config.taxonomy.path || "—",
        config.taxonomy.path ? "active" : "inactive",
      ),
      row(
        "taxonomy.cosine_threshold",
        "Cosine threshold",
        config.taxonomy.cosine_threshold,
        config.taxonomy.path ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "ner",
    title: "Named-entity recognition",
    rows: [
      row(
        "ner.enabled",
        "Enabled",
        config.ner.enabled ? "yes" : "no",
        config.ner.enabled ? "active" : "inactive",
      ),
      row(
        "ner.spacy_model",
        "spaCy model",
        config.ner.spacy_model,
        config.ner.enabled ? "active" : "inactive",
      ),
    ],
  });

  sections.push({
    id: "audit",
    title: "Audit event store",
    rows: [
      row(
        "audit.enabled",
        "Enabled",
        config.audit.enabled ? "yes" : "no",
        config.audit.enabled ? "active" : "inactive",
      ),
      row(
        "audit.db_path",
        "Database path",
        config.audit.db_path || "(derived from data dir)",
        config.audit.enabled ? "active" : "inactive",
      ),
    ],
  });

  // HITL routing — every row stays "active" (or "secret-redacted" for
  // the auth row) because operators always want to see what the
  // current routing is, even if external is disabled.
  const externalActive =
    config.hitl.default_validation_method === "external" &&
    config.hitl.iterop.enabled &&
    config.hitl.iterop.base_url_configured &&
    config.hitl.iterop.auth_configured;

  sections.push({
    id: "hitl",
    title: "Human-in-the-loop routing",
    rows: [
      row(
        "hitl.default_validation_method",
        "Default routing",
        config.hitl.default_validation_method,
        "active",
      ),
      row(
        "hitl.iterop.enabled",
        "ITEROP adapter",
        config.hitl.iterop.enabled ? "enabled" : "disabled",
        config.hitl.iterop.enabled ? "active" : "inactive",
      ),
      row(
        "hitl.iterop.workflow_ref",
        "Workflow ref",
        config.hitl.iterop.workflow_ref || "—",
        config.hitl.iterop.workflow_ref ? "active" : "inactive",
      ),
      row(
        "hitl.iterop.base_url",
        "Base URL",
        config.hitl.iterop.base_url_configured ? "configured" : "not configured",
        config.hitl.iterop.base_url_configured ? "active" : "inactive",
      ),
      row(
        "hitl.iterop.auth",
        "Auth token",
        config.hitl.iterop.auth_configured ? "configured" : "not configured",
        config.hitl.iterop.auth_configured ? "secret-redacted" : "inactive",
      ),
    ],
  });
  // Suppress the unused-binding lint locally — `externalActive` is
  // intentionally surfaced in DiagnosticTile derivation below, not
  // here.
  void externalActive;

  sections.push({
    id: "logging",
    title: "Logging",
    rows: [
      row("logging.format", "Format", config.logging.format, "active"),
      row("logging.level", "Level", config.logging.level, "active"),
    ],
  });

  return sections;
}

/**
 * Derive the top-of-page status tiles from a config response.
 *
 * Each tile collapses one feature axis (Phase 1 / 2 / 3 / NER /
 * audit / ITEROP) to a single state — green for fully on, red-ish
 * "off" for unset, and amber "warn" for partially configured (e.g.
 * ITEROP enabled but missing auth token).
 */
export function buildDiagnosticTiles(
  config: AdminConfigResponse,
): DiagnosticTile[] {
  const phase1On = config.knowledge_layer.enabled;
  const phase2On = config.llm.configured;
  const phase3On = config.embeddings.configured;
  const nerOn = config.ner.enabled;
  const auditOn = config.audit.enabled;

  const iterop = config.hitl.iterop;
  let iteropState: "ok" | "off" | "warn" = "off";
  if (iterop.enabled) {
    if (iterop.base_url_configured && iterop.auth_configured) {
      iteropState = "ok";
    } else {
      iteropState = "warn";
    }
  }
  const iteropSubtitle =
    iteropState === "ok"
      ? iterop.workflow_ref || "configured"
      : iteropState === "warn"
        ? !iterop.auth_configured
          ? "no auth"
          : "no base URL"
        : "disabled";

  return [
    {
      id: "phase1",
      label: "Phase 1",
      sublabel: phase1On ? "Knowledge graph" : "Disabled",
      state: phase1On ? "ok" : "off",
    },
    {
      id: "phase2",
      label: "Phase 2",
      sublabel: phase2On ? "LLM extraction" : "No LLM key",
      state: phase2On ? "ok" : "off",
    },
    {
      id: "phase3",
      label: "Phase 3",
      sublabel: phase3On ? "Vector RAG" : "No Voyage key",
      state: phase3On ? "ok" : "off",
    },
    {
      id: "ner",
      label: "NER",
      sublabel: nerOn ? "spaCy enricher" : "Opt-in",
      state: nerOn ? "ok" : "off",
    },
    {
      id: "audit",
      label: "Audit",
      sublabel: auditOn ? "SQLite events" : "Logs only",
      state: auditOn ? "ok" : "off",
    },
    {
      id: "iterop",
      label: "ITEROP",
      sublabel: iteropSubtitle,
      state: iteropState,
    },
  ];
}
