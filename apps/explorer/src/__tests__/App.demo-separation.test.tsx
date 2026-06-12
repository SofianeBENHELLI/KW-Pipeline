/**
 * Sprint 1 — demo/operator data separation.
 *
 * The backend now tags catalog rows with ``origin: "demo" |
 * "operator"`` (migration 0016) so the bundled demo corpus can never
 * be mistaken for production data. On the Explorer side:
 *
 *   1. **Auto-hide rule** — when demo and operator docs coexist and
 *      the operator never chose, demo rows are hidden from the graph
 *      snapshot and the catalog, and the CORPUS rail shows a
 *      "Demo data · hidden (N)" chip.
 *   2. **Chip toggle** — clicking the chip flips visibility; demo
 *      rows come back with a DEMO badge in the catalog.
 *   3. **Pure-demo corpus** — nothing is hidden by default (an
 *      operator who just clicked "Load demo" must see the corpus),
 *      and rows still carry the badge.
 *
 * Same fetch-stub idiom as App.taxonomy-catalog.test.tsx.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type {
  Document as ApiDocument,
  DocumentListResponse,
} from "../api/types";

function makeJson(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fixtureDoc(
  id: string,
  filename: string,
  origin: "operator" | "demo",
): ApiDocument {
  return {
    id,
    original_filename: filename,
    latest_version_id: `${id}-v1`,
    created_at: "2026-04-01T10:00:00Z",
    origin,
    versions: [
      {
        id: `${id}-v1`,
        document_id: id,
        version_number: 1,
        filename,
        content_type: "text/plain",
        file_size: 1024,
        sha256: `hash-${id}`,
        storage_uri: `memory://${id}-v1`,
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-04-01T10:00:00Z",
      },
    ],
  };
}

function makeFetchStub(catalog: DocumentListResponse) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url =
      typeof input === "string"
        ? input
        : ((input as Request).url ?? input.toString());
    if (url.includes("/knowledge/taxonomy")) {
      return makeJson({
        schema_version: "v0.1",
        is_configured: false,
        source_path: null,
        categories: [],
      });
    }
    if (url.includes("/documents") && !url.includes("/versions")) {
      return makeJson(catalog);
    }
    if (url.includes("/knowledge/graph")) {
      return makeJson({
        schema_version: "v0.2",
        nodes: [],
        edges: [],
        next_cursor: null,
      });
    }
    return new Response("{}", { status: 404 });
  });
}

const MIXED: DocumentListResponse = {
  items: [
    fixtureDoc("doc-real", "production_report.txt", "operator"),
    fixtureDoc("doc-demo-1", "quality_iso9001_handbook.txt", "demo"),
    fixtureDoc("doc-demo-2", "supplier_qualification_checklist.txt", "demo"),
  ],
  next_cursor: null,
};

const PURE_DEMO: DocumentListResponse = {
  items: [
    fixtureDoc("doc-demo-1", "quality_iso9001_handbook.txt", "demo"),
    fixtureDoc("doc-demo-2", "supplier_qualification_checklist.txt", "demo"),
  ],
  next_cursor: null,
};

describe("Knowledge Explorer — demo/operator separation (Sprint 1)", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("auto-hides demo rows on a mixed corpus and surfaces the chip", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchStub(MIXED));
    render(<App />);

    const chip = await screen.findByTestId("kx-demo-visibility-toggle");
    expect(chip.textContent).toContain("hidden (2)");

    // Catalog tab only lists the operator row.
    fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
    const table = await screen.findByTestId("kx-catalog-table");
    await waitFor(() => {
      expect(table.textContent).toContain("production_report.txt");
    });
    expect(table.textContent).not.toContain("quality_iso9001_handbook.txt");
    expect(screen.queryAllByTestId("kx-demo-badge")).toHaveLength(0);
  });

  it("chip toggle reveals demo rows with the DEMO badge", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchStub(MIXED));
    render(<App />);

    const chip = await screen.findByTestId("kx-demo-visibility-toggle");
    fireEvent.click(chip);
    expect(chip.textContent).toContain("shown (2)");

    fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
    const table = await screen.findByTestId("kx-catalog-table");
    await waitFor(() => {
      expect(table.textContent).toContain("quality_iso9001_handbook.txt");
    });
    expect(table.textContent).toContain("production_report.txt");
    expect(screen.getAllByTestId("kx-demo-badge")).toHaveLength(2);
  });

  it("pure-demo corpus stays visible by default (auto rule)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(makeFetchStub(PURE_DEMO));
    render(<App />);

    const chip = await screen.findByTestId("kx-demo-visibility-toggle");
    expect(chip.textContent).toContain("shown (2)");

    fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
    const table = await screen.findByTestId("kx-catalog-table");
    await waitFor(() => {
      expect(table.textContent).toContain("quality_iso9001_handbook.txt");
    });
    expect(screen.getAllByTestId("kx-demo-badge")).toHaveLength(2);
  });
});
