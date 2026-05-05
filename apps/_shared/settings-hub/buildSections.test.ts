/**
 * Unit tests for the data transforms used by ``<SettingsHub/>``.
 *
 * These tests are picked up by ``apps/widget``'s vitest run via the
 * ``test.include`` extension in ``apps/widget/vitest.config.ts``.
 */

import { describe, expect, it } from "vitest";

import {
  buildDiagnosticTiles,
  buildSettingsSections,
} from "./buildSections";
import type { AdminConfigResponse } from "./types";

const DEFAULT_CONFIG: AdminConfigResponse = {
  schema_version: "v0.1",
  upload: { max_bytes: 50 * 1024 * 1024, allowed_content_types: ["text/plain"] },
  cors: { allowed_origins: [], allowed_origin_regex: "" },
  persistence: { persistent: false, data_dir: ".kw-pipeline" },
  knowledge_layer: {
    enabled: false,
    neo4j_configured: false,
    neo4j_database: "neo4j",
  },
  llm: { configured: false, model: "", max_input_tokens_per_document: 0 },
  embeddings: { configured: false, model: "voyage-3" },
  taxonomy: { path: "", cosine_threshold: 0.55 },
  ner: { enabled: false, spacy_model: "en_core_web_sm" },
  audit: { enabled: false, db_path: "" },
  hitl: {
    default_validation_method: "human",
    iterop: {
      enabled: false,
      workflow_ref: "",
      base_url_configured: false,
      auth_configured: false,
    },
    force_auto_corpus: false,
  },
  logging: { format: "text", level: "INFO" },
};

function withConfig(overrides: Partial<AdminConfigResponse>): AdminConfigResponse {
  return { ...DEFAULT_CONFIG, ...overrides };
}

describe("buildDiagnosticTiles", () => {
  it("marks every feature ``off`` on the default deployment", () => {
    const tiles = buildDiagnosticTiles(DEFAULT_CONFIG);
    for (const tile of tiles) {
      expect(tile.state).toBe("off");
    }
    expect(tiles.map((t) => t.id)).toEqual([
      "phase1",
      "phase2",
      "phase3",
      "ner",
      "audit",
      "iterop",
    ]);
  });

  it("flips Phase 2 to ``ok`` once the LLM key is configured", () => {
    const tiles = buildDiagnosticTiles(
      withConfig({ llm: { configured: true, model: "claude-sonnet-4-5", max_input_tokens_per_document: 0 } }),
    );
    const phase2 = tiles.find((t) => t.id === "phase2");
    expect(phase2?.state).toBe("ok");
  });

  it("renders ITEROP as ``warn`` when enabled but auth is missing", () => {
    const tiles = buildDiagnosticTiles(
      withConfig({
        hitl: {
          default_validation_method: "external",
          iterop: {
            enabled: true,
            workflow_ref: "WF-A",
            base_url_configured: true,
            auth_configured: false,
          },
        },
      }),
    );
    const tile = tiles.find((t) => t.id === "iterop");
    expect(tile?.state).toBe("warn");
    expect(tile?.sublabel).toBe("no auth");
  });

  it("renders ITEROP as ``ok`` and shows the workflow ref when fully configured", () => {
    const tiles = buildDiagnosticTiles(
      withConfig({
        hitl: {
          default_validation_method: "external",
          iterop: {
            enabled: true,
            workflow_ref: "WF-KW-DOC-REVIEW-001",
            base_url_configured: true,
            auth_configured: true,
          },
        },
      }),
    );
    const tile = tiles.find((t) => t.id === "iterop");
    expect(tile?.state).toBe("ok");
    expect(tile?.sublabel).toBe("WF-KW-DOC-REVIEW-001");
  });
});

describe("buildSettingsSections", () => {
  it("emits one section per feature axis on the default config", () => {
    const sections = buildSettingsSections(DEFAULT_CONFIG);
    expect(sections.map((s) => s.id)).toEqual([
      "upload",
      "cors",
      "persistence",
      "knowledge_layer",
      "llm",
      "embeddings",
      "taxonomy",
      "ner",
      "audit",
      "hitl",
      "logging",
    ]);
  });

  it("marks every dependent row inactive when the parent feature is off", () => {
    const sections = buildSettingsSections(DEFAULT_CONFIG);
    const llm = sections.find((s) => s.id === "llm");
    // Every row in the LLM section is inactive when configured=false.
    expect(llm?.rows.every((r) => r.status === "inactive")).toBe(true);
  });

  it("flips dependent rows to active when the parent feature is configured", () => {
    const sections = buildSettingsSections(
      withConfig({
        llm: {
          configured: true,
          model: "claude-opus-4-7",
          max_input_tokens_per_document: 1000,
        },
      }),
    );
    const llm = sections.find((s) => s.id === "llm");
    const apiKeyRow = llm?.rows.find((r) => r.key === "llm.configured");
    const modelRow = llm?.rows.find((r) => r.key === "llm.model");
    // The API-key row is always ``secret-redacted`` when configured —
    // never ``active`` — because the value is intentionally redacted.
    expect(apiKeyRow?.status).toBe("secret-redacted");
    expect(apiKeyRow?.value).toBe("configured");
    // Sibling rows (model, token cap) become active.
    expect(modelRow?.status).toBe("active");
    expect(modelRow?.value).toBe("claude-opus-4-7");
  });

  it("includes the ITEROP workflow ref verbatim when set", () => {
    const sections = buildSettingsSections(
      withConfig({
        hitl: {
          default_validation_method: "external",
          iterop: {
            enabled: true,
            workflow_ref: "WF-KW-DOC-REVIEW-001",
            base_url_configured: true,
            auth_configured: true,
          },
        },
      }),
    );
    const hitl = sections.find((s) => s.id === "hitl");
    const wf = hitl?.rows.find((r) => r.key === "hitl.iterop.workflow_ref");
    expect(wf?.value).toBe("WF-KW-DOC-REVIEW-001");
    expect(wf?.status).toBe("active");
    // The auth row stays ``secret-redacted`` even when configured.
    const auth = hitl?.rows.find((r) => r.key === "hitl.iterop.auth");
    expect(auth?.status).toBe("secret-redacted");
    expect(auth?.value).toBe("configured");
  });

  it("attaches a human-readable help string to every row", () => {
    const sections = buildSettingsSections(DEFAULT_CONFIG);
    for (const section of sections) {
      for (const row of section.rows) {
        // Every row carries its English description for the hover tip.
        expect(row.help.length).toBeGreaterThan(0);
      }
    }
  });
});
