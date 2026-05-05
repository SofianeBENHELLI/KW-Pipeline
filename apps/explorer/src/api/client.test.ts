/**
 * Smoke tests for the explorer's API client (audit P0 #230 first
 * slice).
 *
 * Mirrors apps/widget/src/api/client.test.ts; both confirm that the
 * shared ``ApiError`` re-export keeps its class identity across both
 * import paths.
 */

import { describe, expect, it } from "vitest";

import { ApiError as SharedApiError } from "../../../_shared/api-core";
import { ApiError as ExplorerApiError, getApiBaseUrl } from "./client";

describe("explorer api client", () => {
  it("ApiError shares class identity with @kw-pipeline/_shared/api-core", () => {
    const err = new ExplorerApiError(
      503,
      "Vector search disabled",
      "KW_VECTOR_SEARCH_DISABLED",
    );
    expect(err).toBeInstanceOf(SharedApiError);
    expect(err).toBeInstanceOf(ExplorerApiError);
  });

  it("getApiBaseUrl falls back to http://localhost:8000 with no widget setting", () => {
    expect(getApiBaseUrl()).toBe("http://localhost:8000");
  });
});
