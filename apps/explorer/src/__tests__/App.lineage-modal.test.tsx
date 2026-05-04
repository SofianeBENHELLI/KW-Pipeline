/**
 * Tests for the version-history modal (PR ``feat/explorer-lineage-modal``).
 *
 *   1. Click the v{N} badge on a multi-version doc → modal opens with
 *      every version sorted DESC.
 *   2. Click backdrop → modal closes.
 *   3. ESC → modal closes.
 *   4. Single-version doc → badge stays inert (no button, no modal).
 *   5. ``v1 VALIDATED → v2 SUPERSEDED → v3 VALIDATED`` lineage → the
 *      v2 row carries the "→ replaced by v3" caption derived
 *      client-side (per ADR-025 only ``VALIDATED → SUPERSEDED`` is
 *      legal, so the supersede chain is always to the next-higher
 *      VALIDATED sibling).
 *
 * Mounting strategy mirrors the other ``__tests__/`` files: stub
 * ``fetch`` and ``Element.prototype.scrollIntoView`` / ``scrollTo``,
 * render ``<App />``, and drive the modal through the catalog table
 * (the same affordance the cluster rail also uses, but the catalog is
 * the most deterministic surface in JSDOM — sample-fallback data has
 * v=1 only, so we wouldn't see the modal otherwise).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { buildRows } from "../components/LineageModal";
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

interface VersionSpec {
  status: string;
  duplicate_of_version_id?: string | null;
}

function fixtureDoc(
  id: string,
  filename: string,
  versionSpecs: VersionSpec[],
): ApiDocument {
  const versions = versionSpecs.map((spec, i) => {
    const versionNumber = i + 1;
    return {
      id: `${id}-v${versionNumber}`,
      document_id: id,
      version_number: versionNumber,
      filename,
      content_type: "application/pdf",
      file_size: 1024 * versionNumber,
      sha256: `${id}sha${versionNumber}deadbeefcafebabe1234567890abcdef`,
      storage_uri: `memory://${id}-v${versionNumber}`,
      // Cast widens to the API enum; SUPERSEDED isn't in the literal
      // union yet (lands with ADR-025's wire change) so we erase to
      // ``any`` at the boundary to keep the typecheck honest while
      // still surfacing the value to the modal.
      status: spec.status as ApiDocument["versions"][number]["status"],
      duplicate_of_version_id: spec.duplicate_of_version_id ?? null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: `2026-04-0${versionNumber}T10:00:00Z`,
    };
  });
  return {
    id,
    original_filename: filename,
    latest_version_id: versions[versions.length - 1]!.id,
    created_at: "2026-04-01T10:00:00Z",
    versions,
  };
}

function makeFetchStub(catalog: DocumentListResponse) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : (input as Request).url ?? input.toString();
    if (url.endsWith("/knowledge/taxonomy")) {
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
      return makeJson({ schema_version: "v0.2", nodes: [], edges: [], next_cursor: null });
    }
    return new Response("{}", { status: 404 });
  });
}

async function openCatalog() {
  await waitFor(() => {
    expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
  });
  fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
  return await screen.findByTestId("kx-catalog-table");
}

describe("LineageModal — version history modal", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── 1. Multi-version → modal opens, rows sorted DESC ─────────────────────
  it("opens the modal with every version sorted DESC by version_number", async () => {
    const doc = fixtureDoc("doc-multi", "Hybrid Work Policy.pdf", [
      { status: "VALIDATED" },
      { status: "VALIDATED" },
      { status: "VALIDATED" },
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchStub({ items: [doc], next_cursor: null }),
    );

    render(<App />);
    const table = await openCatalog();

    // Click the v{N} badge button, NOT the row, so we exercise the
    // dedicated modal-open affordance.
    const row = table.querySelector('[data-doc-id="doc-multi"]') as HTMLElement;
    expect(row).not.toBeNull();
    const badge = row.querySelector(
      '[data-testid="kx-version-badge-button"]',
    ) as HTMLElement | null;
    expect(badge).not.toBeNull();
    fireEvent.click(badge!);

    const modal = await screen.findByTestId("kx-lineage-modal");
    expect(modal).not.toBeNull();
    // Header surfaces the filename.
    expect(screen.getByTestId("kx-lineage-doc-title").textContent).toBe(
      "Hybrid Work Policy.pdf",
    );
    // List has all 3 rows, sorted DESC.
    const rows = modal.querySelectorAll('[data-version-number]');
    expect(rows.length).toBe(3);
    expect(rows[0].getAttribute("data-version-number")).toBe("3");
    expect(rows[1].getAttribute("data-version-number")).toBe("2");
    expect(rows[2].getAttribute("data-version-number")).toBe("1");
    // Latest chip is on the v3 row.
    expect(rows[0].querySelector('[data-testid="kx-lineage-latest-chip"]')).not.toBeNull();
    expect(rows[1].querySelector('[data-testid="kx-lineage-latest-chip"]')).toBeNull();
  });

  // ── 2. Click backdrop closes ────────────────────────────────────────────
  it("closes when the backdrop is clicked", async () => {
    const doc = fixtureDoc("doc-multi", "Two Versions.pdf", [
      { status: "VALIDATED" },
      { status: "VALIDATED" },
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchStub({ items: [doc], next_cursor: null }),
    );

    render(<App />);
    const table = await openCatalog();
    const badge = table
      .querySelector('[data-doc-id="doc-multi"]')!
      .querySelector('[data-testid="kx-version-badge-button"]') as HTMLElement;
    fireEvent.click(badge);
    await screen.findByTestId("kx-lineage-modal");

    const backdrop = screen.getByTestId("kx-lineage-backdrop");
    fireEvent.click(backdrop);
    await waitFor(() => {
      expect(screen.queryByTestId("kx-lineage-modal")).toBeNull();
    });
  });

  // ── 3. ESC closes ────────────────────────────────────────────────────────
  it("closes when ESC is pressed", async () => {
    const doc = fixtureDoc("doc-multi", "Two Versions.pdf", [
      { status: "VALIDATED" },
      { status: "VALIDATED" },
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchStub({ items: [doc], next_cursor: null }),
    );

    render(<App />);
    const table = await openCatalog();
    const badge = table
      .querySelector('[data-doc-id="doc-multi"]')!
      .querySelector('[data-testid="kx-version-badge-button"]') as HTMLElement;
    fireEvent.click(badge);
    await screen.findByTestId("kx-lineage-modal");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByTestId("kx-lineage-modal")).toBeNull();
    });
  });

  // ── 4. Single-version → no button, no modal ──────────────────────────────
  it("renders the v{N} badge as non-interactive when versionCount === 1", async () => {
    const doc = fixtureDoc("doc-single", "Single.pdf", [{ status: "VALIDATED" }]);
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchStub({ items: [doc], next_cursor: null }),
    );

    render(<App />);
    const table = await openCatalog();
    const row = table.querySelector('[data-doc-id="doc-single"]') as HTMLElement;
    expect(row).not.toBeNull();
    // No interactive badge button — only the static span.
    expect(row.querySelector('[data-testid="kx-version-badge-button"]')).toBeNull();
    // The v1 badge text is still present.
    expect(row.textContent).toMatch(/v1/);
    // No modal mounted.
    expect(screen.queryByTestId("kx-lineage-modal")).toBeNull();
  });

  // ── 5. Supersede chain — v1 SUPERSEDED, v2 SUPERSEDED, v3 VALIDATED ──────
  it("derives the supersede chain client-side: v1→v2, v2→v3", async () => {
    // Per ADR-025 only ``VALIDATED → SUPERSEDED`` is legal — so when
    // v3 lands as VALIDATED, both v1 and v2 are SUPERSEDED siblings
    // (each replaced by the next VALIDATED version_number, which in
    // a strict supersede chain is the immediately next one).
    const doc = fixtureDoc("doc-chain", "Lineage.pdf", [
      { status: "SUPERSEDED" },
      { status: "SUPERSEDED" },
      { status: "VALIDATED" },
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchStub({ items: [doc], next_cursor: null }),
    );

    render(<App />);
    const table = await openCatalog();
    const badge = table
      .querySelector('[data-doc-id="doc-chain"]')!
      .querySelector('[data-testid="kx-version-badge-button"]') as HTMLElement;
    fireEvent.click(badge);
    const modal = await screen.findByTestId("kx-lineage-modal");

    // v2 row carries "→ replaced by v3" — the next-higher VALIDATED
    // sibling. (In this fixture both v1 and v2 collapse to v3 because
    // there's no other VALIDATED row between them.)
    const v2Caption = modal.querySelector('[data-testid="kx-lineage-replaced-by-2"]');
    expect(v2Caption).not.toBeNull();
    expect(v2Caption!.textContent).toMatch(/replaced by v3/);

    // v1 row carries the same "→ replaced by v3" caption — the
    // earliest VALIDATED sibling above v1 is v3.
    const v1Caption = modal.querySelector('[data-testid="kx-lineage-replaced-by-1"]');
    expect(v1Caption).not.toBeNull();
    expect(v1Caption!.textContent).toMatch(/replaced by v3/);

    // v3 is the latest: no replacement caption.
    const v3Row = modal.querySelector('[data-version-number="3"]') as HTMLElement;
    expect(v3Row.querySelector('[data-testid^="kx-lineage-replaced-by-"]')).toBeNull();
    expect(v3Row.classList.contains("kx-lineage-row--latest")).toBe(true);
  });

  // ── 6. The DetailPanel "View history" link also opens the modal ──────────
  it("the DetailPanel 'View history' link opens the modal too", async () => {
    const doc = fixtureDoc("doc-multi", "Multi.pdf", [
      { status: "VALIDATED" },
      { status: "VALIDATED" },
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeFetchStub({ items: [doc], next_cursor: null }),
    );

    render(<App />);
    const table = await openCatalog();
    // Click the row (NOT the badge) so the DetailPanel mounts with
    // the doc's metadata. The catalog row click → onSelectDocument →
    // DetailPanel renders the Versions section with the "View
    // history" link.
    const row = table.querySelector('[data-doc-id="doc-multi"]') as HTMLElement;
    fireEvent.click(row);

    const link = await screen.findByTestId("kx-versions-history-link");
    fireEvent.click(link);
    await screen.findByTestId("kx-lineage-modal");
  });
});

// ─── Pure unit tests for buildRows — no React / DOM ────────────────────────

describe("buildRows — pure derivation", () => {
  it("flags the highest version_number as latest", () => {
    const rows = buildRows([
      { id: "a", versionNumber: 1, status: "SUPERSEDED", createdAt: "", filename: "a" },
      { id: "b", versionNumber: 2, status: "VALIDATED", createdAt: "", filename: "b" },
    ]);
    expect(rows[0].version.versionNumber).toBe(2);
    expect(rows[0].isLatest).toBe(true);
    expect(rows[1].isLatest).toBe(false);
    expect(rows[1].isSuperseded).toBe(true);
  });

  it("derives 'duplicate of v{X}' from duplicateOfVersionId when status is DUPLICATE_DETECTED", () => {
    const rows = buildRows([
      { id: "v1", versionNumber: 1, status: "VALIDATED", createdAt: "", filename: "f" },
      {
        id: "v2",
        versionNumber: 2,
        status: "DUPLICATE_DETECTED",
        createdAt: "",
        filename: "f",
        duplicateOfVersionId: "v1",
      },
    ]);
    const dupRow = rows.find((r) => r.version.versionNumber === 2)!;
    expect(dupRow.duplicateOfVersion).toBe(1);
  });
});
