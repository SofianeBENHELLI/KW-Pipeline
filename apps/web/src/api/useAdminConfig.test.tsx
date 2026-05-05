/**
 * Tests for ``useAdminConfig`` (EPIC-A A.8, #215).
 *
 * Pins the four states the hook can land in:
 *   - ``loading``   — initial render, fetch in flight.
 *   - ``ok``        — config available; banner reads from it.
 *   - ``forbidden`` — caller is not admin (403 from /admin/config).
 *   - ``error``     — fetch failed for any other reason.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAdminConfig } from "./useAdminConfig";
import type { AdminConfigResponse } from "../../../_shared/settings-hub";

function adminConfig(overrides: Partial<AdminConfigResponse> = {}): AdminConfigResponse {
  return {
    schema_version: "v0.1",
    upload: { max_bytes: 50 * 1024 * 1024, allowed_content_types: ["text/plain"] },
    cors: { allowed_origins: [], allowed_origin_regex: "" },
    persistence: { persistent: false, data_dir: ".kw-pipeline" },
    knowledge_layer: { enabled: false, neo4j_configured: false, neo4j_database: "neo4j" },
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
    ...overrides,
  };
}

describe("useAdminConfig", () => {
  beforeEach(() => {
    // Default: every fetch returns a normal config snapshot. Tests
    // override the spy explicitly when they want a different shape.
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(adminConfig()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts in loading state and resolves to ok when the response is 200", async () => {
    const { result } = renderHook(() => useAdminConfig("http://localhost:8000"));
    expect(result.current.status).toBe("loading");

    await waitFor(() => {
      expect(result.current.status).toBe("ok");
    });
    expect(result.current.config?.hitl.force_auto_corpus).toBe(false);
  });

  it("surfaces force_auto_corpus=true through the config", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            adminConfig({
              hitl: {
                default_validation_method: "human",
                iterop: {
                  enabled: false,
                  workflow_ref: "",
                  base_url_configured: false,
                  auth_configured: false,
                },
                force_auto_corpus: true,
              },
            }),
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const { result } = renderHook(() => useAdminConfig("http://localhost:8000"));
    await waitFor(() => {
      expect(result.current.status).toBe("ok");
    });
    expect(result.current.config?.hitl.force_auto_corpus).toBe(true);
  });

  it("lands in forbidden state when /admin/config returns 403", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: "Forbidden" }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const { result } = renderHook(() => useAdminConfig("http://localhost:8000"));
    await waitFor(() => {
      expect(result.current.status).toBe("forbidden");
    });
    expect(result.current.config).toBeNull();
  });

  it("lands in error state on network failure", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.reject(new Error("network down")),
    );

    const { result } = renderHook(() => useAdminConfig("http://localhost:8000"));
    await waitFor(() => {
      expect(result.current.status).toBe("error");
    });
    expect(result.current.error?.message).toMatch(/network down/);
  });

  it("ignores AbortError so unmount during fetch is silent", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.reject(new DOMException("Aborted", "AbortError")),
    );

    const { result } = renderHook(() => useAdminConfig("http://localhost:8000"));
    // Allow microtasks + a bit more so the rejection has been processed
    // by the catch handler.
    await act(async () => {
      await Promise.resolve();
    });
    // Stays in loading — the AbortError branch never sets state.
    expect(result.current.status).toBe("loading");
  });
});
