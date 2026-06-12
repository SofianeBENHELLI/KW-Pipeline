import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import type { ApiDocument, ListDocumentsResponse } from "./api/types";

// Wrap the App in a MemoryRouter so the top-level <Routes> tree
// added in D.9 has a routing context. ``initialEntries=["/"]`` keeps
// every legacy test on the reviewer workbench. Since the cutover (PR
// flipping `/` to `/kf/review`), the legacy surface lives at
// /legacy/* — this helper points there so the existing assertions
// keep validating the legacy code path. New surfaces have their own
// tests under src/orb/.
function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/legacy"]}>
      <App />
    </MemoryRouter>,
  );
}

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
  origin: "operator",
  id: "doc-policy-001",
  original_filename: "supplier-quality-policy.txt",
  latest_version_id: "ver-policy-002",
  created_at: "2026-04-30T08:42:00Z",
  archived_at: null,
  versions: [FIXTURE_VERSION],
  scopes: [],
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
    renderApp();

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
    renderApp();

    await waitFor(() => {
      expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    });

    // #292 — Orbital is read-only for ingestion; upload UI lives in Forge now.
    expect(
      screen.queryByRole("button", { name: /Upload document/i }),
    ).not.toBeInTheDocument();
  });

  it("shows an error message when the API call fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));

    renderApp();

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    expect(screen.getByText(/Failed to load documents/i)).toBeInTheDocument();
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("surfaces a banner when the ?document=… deep link points at an unknown id (#292 §4)", async () => {
    // Drop the deep-link param into ``window.location.search`` before
    // mount so ``useDocumentCatalog``'s mount-time effect reads it.
    const initialSearch = window.location.search;
    window.history.replaceState({}, "", "/?document=doc-not-here");
    try {
      renderApp();

      // Banner appears once the catalog finishes its first load and
      // doesn't find the requested doc.
      const banner = await screen.findByTestId("deep-link-error-banner");
      expect(banner).toHaveTextContent(/doc-not-here/);

      // The deep-link param is stripped from the URL on mount so a
      // refresh doesn't re-trigger the auto-select / banner.
      expect(window.location.search).not.toContain("document=");

      // Dismissing the banner removes it from the DOM.
      fireEvent.click(
        screen.getByRole("button", { name: /Dismiss deep link error/i }),
      );
      expect(screen.queryByTestId("deep-link-error-banner")).toBeNull();
    } finally {
      window.history.replaceState({}, "", `/${initialSearch}`);
    }
  });

  it("auto-selects the row and clears the URL when ?document=… resolves to a known doc (#292 §4)", async () => {
    const initialSearch = window.location.search;
    window.history.replaceState({}, "", "/?document=doc-policy-001");
    try {
      renderApp();

      // The selected row's review pane lands on the requested doc.
      await waitFor(() => {
        expect(
          screen.getByRole("heading", { name: /supplier-quality-policy\.txt/i }),
        ).toBeInTheDocument();
      });
      // No 404 banner — the doc resolved.
      expect(screen.queryByTestId("deep-link-error-banner")).toBeNull();
      // Param has been stripped.
      expect(window.location.search).not.toContain("document=");
    } finally {
      window.history.replaceState({}, "", `/${initialSearch}`);
    }
  });

  it("shows empty state when the API returns no documents", async () => {
    vi.restoreAllMocks();
    // Use ``mockImplementation`` so each fetch call gets a FRESH Response.
    // ``mockResolvedValue`` would reuse the same Response object across
    // every call, and React 19 strict-mode's double-render fires the
    // same useEffect twice — the second read of the same Response
    // would fail with "Body is unusable: Body has already been read."
    // This was a latent test bug; the retry wrapper in this PR added a
    // microtask that exposed it.
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse({ items: [], next_cursor: null })),
    );

    renderApp();

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

    renderApp();

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
    const { container } = renderApp();

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

    renderApp();

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
