import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

describe("App", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = typeof input === "string" ? input : input.toString();

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
});
