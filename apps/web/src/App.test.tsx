import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axe from "axe-core";
import App from "./App";
import type { ApiDocument, ListDocumentsResponse } from "./api/types";

// ─── Fixture data ────────────────────────────────────────────────────────────

const FIXTURE_VERSION = {
  id: "ver-policy-002",
  document_id: "doc-policy-001",
  version_number: 2,
  filename: "supplier-quality-policy.txt",
  content_type: "text/plain",
  file_size: 1840,
  sha256: "6ad1c5de1e5a2fd3f8db4c8cfeb61a810f83f8bd3fd3f0b10d6b8e9d5875f002",
  storage_uri: "file://policy-002",
  status: "NEEDS_REVIEW" as const,
  duplicate_of_version_id: null,
  failure_reason: null,
  reviewer_note: null,
  reviewed_at: null,
  created_at: "2026-04-30T08:42:00Z",
};

const FIXTURE_DOCUMENT: ApiDocument = {
  id: "doc-policy-001",
  original_filename: "supplier-quality-policy.txt",
  latest_version_id: "ver-policy-002",
  created_at: "2026-04-30T08:42:00Z",
  versions: [FIXTURE_VERSION],
};

const FIXTURE_LIST: ListDocumentsResponse = {
  items: [FIXTURE_DOCUMENT],
  next_cursor: null,
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ─── Tests ───────────────────────────────────────────────────────────────────

// `openapi-fetch` invokes `fetch` with a Request object (not a URL string).
function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

describe("App", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);

        if (url.includes("/documents?")) {
          return Promise.resolve(makeJsonResponse(FIXTURE_LIST));
        }
        // Extraction and semantic requests return 404 for this fixture
        if (url.includes("/extraction") || url.includes("/semantic")) {
          return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the compact pipeline widget and review workspace after loading", async () => {
    render(<App />);

    // After the async calls resolve, the full UI appears
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /KW Pipeline/i })).toBeInTheDocument();
    });

    // Wait for detail loading to also finish (extraction/semantic 404s resolve)
    await waitFor(() => {
      expect(screen.getByText(/No extraction output is available\./i)).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: /Recent documents/i })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /supplier-quality-policy\.txt/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Raw extraction/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Semantic output/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Markdown preview/i })).toBeInTheDocument();
  });

  it("surfaces review and failure states in the widget", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    });

    expect(screen.getByRole("button", { name: /Upload document/i })).toBeInTheDocument();
  });

  it("shows an error message when the API call fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    expect(screen.getByText(/Failed to load documents/i)).toBeInTheDocument();
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("shows empty state when the API returns no documents", async () => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ items: [], next_cursor: null }),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/No documents found/i)).toBeInTheDocument();
    });
  });

  it("error state surfaces a Retry button that re-runs the catalog fetch", async () => {
    let attempts = 0;
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/documents?")) {
          attempts += 1;
          if (attempts === 1) {
            return Promise.reject(new Error("Network error"));
          }
          return Promise.resolve(makeJsonResponse(FIXTURE_LIST));
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load documents/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /^Retry$/i }));

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: /KW Pipeline/i }),
      ).toBeInTheDocument();
    });
    expect(attempts).toBe(2);
  });

  it("has no axe-core a11y violations on the loaded review surface", async () => {
    const { container } = render(<App />);

    // Wait for initial load + detail load to settle so axe sees the
    // full reviewer surface, not the loading state.
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /KW Pipeline/i })).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/No extraction output is available\./i)).toBeInTheDocument();
    });

    // color-contrast needs a real layout engine; jsdom can't compute
    // it. Region requires a <main>/<nav> hierarchy our shell already
    // provides via <main aria-label>. Keep the rest at default.
    const results = await axe.run(container, {
      rules: {
        "color-contrast": { enabled: false },
      },
    });
    expect(results.violations).toEqual([]);
  });

  it("refetches the selected document and re-renders the status badge after validate", async () => {
    const SEMANTIC = {
      id: "sem-001",
      document_version_id: "ver-policy-002",
      schema_version: "v0.1",
      document_profile: {
        title: "Policy",
        document_type: "unknown",
        purpose: null,
        audience: null,
        executive_summary: null,
      },
      sections: [],
      assets: [],
      warnings: [],
      source_references: [],
      validation_status: "needs_review" as const,
      markdown: "",
      created_at: "2026-04-30T08:42:00Z",
    };

    // Two list responses: first the original NEEDS_REVIEW doc, then a
    // VALIDATED version after the validate POST. Sequencing makes the
    // second list call resolve to the updated fixture.
    let getDocumentCalls = 0;
    let validateCalled = false;
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        const method = (input as Request).method ?? "GET";
        const validatedDoc: ApiDocument = {
          ...FIXTURE_DOCUMENT,
          versions: [{ ...FIXTURE_VERSION, status: "VALIDATED" as const }],
        };
        const validatedList: ListDocumentsResponse = {
          items: [validatedDoc],
          next_cursor: null,
        };

        if (url.includes("/documents?")) {
          return Promise.resolve(
            makeJsonResponse(validateCalled ? validatedList : FIXTURE_LIST),
          );
        }
        if (url.match(/\/documents\/doc-policy-001$/)) {
          getDocumentCalls += 1;
          return Promise.resolve(makeJsonResponse(validatedDoc));
        }
        if (url.endsWith("/extraction")) {
          return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
        }
        if (url.endsWith("/semantic") && method === "GET") {
          return Promise.resolve(makeJsonResponse(SEMANTIC));
        }
        if (url.endsWith("/validate")) {
          validateCalled = true;
          return Promise.resolve(
            makeJsonResponse({ ...SEMANTIC, validation_status: "validated" }),
          );
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /KW Pipeline/i })).toBeInTheDocument();
    });

    // Initial NEEDS_REVIEW state — wait for semantic to load (validate
    // becomes clickable when version is NEEDS_REVIEW).
    const validate = await screen.findByRole("button", { name: /^Validate$/i });
    fireEvent.click(validate);

    // After validate, App should call getDocument(id) to refresh the
    // selected document — verify the call landed and the status badge
    // updated to "Validated".
    await waitFor(() => {
      expect(getDocumentCalls).toBeGreaterThan(0);
    });
    await waitFor(() => {
      expect(screen.getAllByText("Validated").length).toBeGreaterThan(0);
    });
  });
});
