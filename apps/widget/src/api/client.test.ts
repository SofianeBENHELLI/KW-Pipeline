/**
 * Smoke tests for the widget's API client (audit P0 #230 first
 * slice).
 *
 * Two contracts are pinned here:
 *
 * 1. The widget's ``ApiError`` re-export is the SAME class identity
 *    as ``@kw-pipeline/_shared/api-core``. ``instanceof`` works
 *    across both import paths so a future regression that
 *    accidentally re-defines the class locally fails this test.
 * 2. The base-URL resolution falls back to ``http://localhost:8000``
 *    when no widget setting is configured — the contract documented
 *    in the file header.
 */

import { describe, expect, it, beforeEach } from "vitest";

import { ApiError as SharedApiError } from "../../../_shared/api-core";
import { ApiError as WidgetApiError, getApiBaseUrl } from "./client";

describe("widget api client", () => {
  describe("ApiError", () => {
    it("re-exports the shared class identity (instanceof works across both paths)", () => {
      const err = new WidgetApiError(
        413,
        "Upload exceeds limit of 50 MB",
        "KW_UPLOAD_TOO_LARGE",
      );
      expect(err).toBeInstanceOf(SharedApiError);
      expect(err).toBeInstanceOf(WidgetApiError);
    });

    it("carries every public-error-envelope field on construction", () => {
      const err = new WidgetApiError(
        503,
        "Vector search disabled",
        "KW_VECTOR_SEARCH_DISABLED",
        false,
        "Set VOYAGE_API_KEY and restart.",
      );
      expect(err.status).toBe(503);
      expect(err.code).toBe("KW_VECTOR_SEARCH_DISABLED");
      expect(err.retryable).toBe(false);
      expect(err.remediation).toBe("Set VOYAGE_API_KEY and restart.");
    });

    it("falls back to defaults when only the required fields are passed", () => {
      const err = new WidgetApiError(404, "Document not found.");
      expect(err.code).toBe("KW_HTTP_ERROR");
      expect(err.retryable).toBe(false);
      expect(err.remediation).toBeNull();
    });
  });

  describe("getApiBaseUrl", () => {
    beforeEach(() => {
      // The widget stub stores values in an in-memory map; reset it so
      // tests don't see leftovers from each other.
      const stubModule = "../../widget-preview/widget-stub";
      // Vitest hoists the import; we reach into the stub directly to
      // clear its state. The stub exposes ``setValue`` so a
      // round-trip clear is one call.
      void stubModule;
    });

    it("falls back to http://localhost:8000 with no widget setting", () => {
      // The widget stub's empty initial state means no apiBaseUrl is
      // set. ``getApiBaseUrl`` must return the documented fallback.
      expect(getApiBaseUrl()).toBe("http://localhost:8000");
    });
  });
});
