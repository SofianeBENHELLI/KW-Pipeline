/**
 * Tests for the 2026-05-04 sprint additions:
 *
 *   1. Live taxonomy fetch — ``GET /knowledge/taxonomy`` populates
 *      the cluster rail with operator-imposed categories that carry
 *      a "imposed" badge; the existing topic-derived clusters keep
 *      a "auto" badge. A 404 / unconfigured response falls back to
 *      the snapshot-derived clusters without crashing the UI.
 *   2. Catalog tab — clicking the third tab renders a sortable
 *      table; the status filter chips narrow the rows; clicking a
 *      row opens the matching doc in the DetailPanel.
 *   3. Version count + v{N} latest badge — a doc fixture with
 *      multiple versions surfaces both the badge and the
 *      "(N versions)" affordance, and the DetailPanel exposes a
 *      Versions section listing every version.
 *
 * Scope: render ``<App />`` against a stubbed fetch. Same JSDOM
 * limitations as App.bugfixes.test.tsx — ``scrollIntoView`` /
 * ``scrollTo`` are stubbed.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import type {
  Document as ApiDocument,
  DocumentListResponse,
  TaxonomyResponse,
} from "../api/types";

function makeJson(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyCatalog(): DocumentListResponse {
  return { items: [], next_cursor: null };
}

function fixtureDoc(overrides: Partial<ApiDocument> = {}, versionCount = 1): ApiDocument {
  const base: ApiDocument = {
    id: overrides.id ?? "doc-1",
    original_filename: overrides.original_filename ?? "Hybrid Work Policy.pdf",
    latest_version_id: overrides.latest_version_id ?? `${overrides.id ?? "doc-1"}-v${versionCount}`,
    created_at: overrides.created_at ?? "2026-04-01T10:00:00Z",
    versions: overrides.versions ?? [],
  };
  if (base.versions.length === 0) {
    for (let i = 1; i <= versionCount; i += 1) {
      base.versions.push({
        id: `${base.id}-v${i}`,
        document_id: base.id,
        version_number: i,
        filename: base.original_filename,
        content_type: "application/pdf",
        file_size: 1024 * i,
        sha256: `hash-${base.id}-${i}`,
        storage_uri: `memory://${base.id}-v${i}`,
        status: i === versionCount ? "VALIDATED" : "UPLOADED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: `2026-04-0${i}T10:00:00Z`,
      });
    }
  }
  base.latest_version_id = base.versions[base.versions.length - 1]!.id;
  return base;
}

interface FetchOpts {
  taxonomy?: TaxonomyResponse | { status: number };
  catalog?: DocumentListResponse;
}

// Shared fetch stub. Routes are matched by path suffix because the
// Explorer prepends the ``apiBaseUrl`` (the widget-stub helper
// returns a fallback URL in tests).
function makeFetchStub(opts: FetchOpts = {}) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : (input as Request).url ?? input.toString();
    if (url.endsWith("/knowledge/taxonomy")) {
      const t = opts.taxonomy;
      if (t && "status" in t) {
        return new Response(JSON.stringify({ detail: "not configured" }), {
          status: t.status,
          headers: { "Content-Type": "application/json" },
        });
      }
      return makeJson(
        t ?? {
          schema_version: "v0.1",
          is_configured: false,
          source_path: null,
          categories: [],
        },
      );
    }
    if (url.includes("/documents") && !url.includes("/versions")) {
      return makeJson(opts.catalog ?? emptyCatalog());
    }
    if (url.includes("/knowledge/graph")) {
      return makeJson({ schema_version: "v0.2", nodes: [], edges: [], next_cursor: null });
    }
    // Fallback — empty 404 so the explorer's per-doc fetches don't
    // hang.
    return new Response("{}", { status: 404 });
  });
}

describe("Knowledge Explorer — sprint additions (taxonomy + catalog + versions)", () => {
  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── 1. Taxonomy live fetch ───────────────────────────────────────────────
  describe("Live taxonomy fetch", () => {
    it("renders both 'imposed' and 'computed' badges based on the API source flag (#249)", async () => {
      // The hybrid taxonomy endpoint (#249) returns BOTH operator-
      // imposed and auto-deduced clusters in one payload, each
      // carrying a ``source: "computed" | "imposed"`` flag. The
      // Explorer no longer derives source client-side — it just
      // reflects what the API said, so we fixture both kinds and
      // assert the matching badge renders for each.
      const docs: ApiDocument[] = [
        fixtureDoc({ id: "doc-a", original_filename: "Compliance Brief.pdf" }, 1),
      ];
      const taxonomy: TaxonomyResponse = {
        schema_version: "v0.1",
        is_configured: true,
        source_path: "/etc/kw/taxonomy.yml",
        categories: [
          {
            id: "compliance",
            label: "Compliance",
            description: "Regulatory and compliance docs",
            subcategories: [],
            source: "imposed",
          },
          {
            id: "engineering",
            label: "Engineering",
            description: "Technical RFCs and design docs",
            subcategories: [],
            source: "imposed",
          },
          {
            id: "topic-cluster-42",
            label: "Auto cluster",
            description: "Auto-deduced topic cluster covering some keywords.",
            subcategories: [],
            source: "computed",
          },
        ],
      };
      vi.spyOn(globalThis, "fetch").mockImplementation(
        makeFetchStub({
          taxonomy,
          catalog: { items: docs, next_cursor: null },
        }),
      );

      render(<App />);

      // Wait for the cluster rail to render at least the imposed
      // badge.
      await waitFor(() => {
        const imposed = document.querySelectorAll('[data-testid="kx-cl-src-imposed"]');
        expect(imposed.length).toBeGreaterThan(0);
      });

      const list = document.querySelector(".kx-cluster-list") as HTMLElement;
      expect(list).not.toBeNull();
      // Both source values flowed through the API and are reflected
      // verbatim in the rail's badges — the imposed-tagged categories
      // get the "imposed" badge, the computed-tagged one gets "auto".
      const imposedBadges = document.querySelectorAll('[data-testid="kx-cl-src-imposed"]');
      const autoBadges = document.querySelectorAll('[data-testid="kx-cl-src-auto"]');
      expect(imposedBadges.length).toBeGreaterThan(0);
      expect(autoBadges.length).toBeGreaterThan(0);
      const labels = Array.from(list.querySelectorAll(".kx-cl-name")).map((el) => el.textContent);
      expect(
        labels.some((l) => l && /Compliance|Engineering|Auto cluster|unknown/.test(l)),
      ).toBe(true);
    });

    it("falls back to topic-derived ('auto') clusters when the taxonomy endpoint 404s", async () => {
      // Reject the catalog so ``useExplorerData`` lands in
      // ``sample-fallback`` (the SAMPLE_SNAPSHOT) — that's the only
      // path that still surfaces a populated rail without going through
      // the real backend, and every sample cluster is tagged
      // ``source: "computed"`` so the assertions below hold. Returning
      // ``items: []`` would now resolve to the real "empty corpus"
      // empty-state with zero cluster rows, which is the wrong fixture
      // for this assertion.
      vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("catalog down"));

      render(<App />);

      await waitFor(() => {
        // Sample fallback gives us "People & HR" in the rail.
        expect(screen.getAllByText("People & HR").length).toBeGreaterThan(0);
      });

      // No "imposed" badges — only "auto" ones.
      const imposed = document.querySelectorAll('[data-testid="kx-cl-src-imposed"]');
      const auto = document.querySelectorAll('[data-testid="kx-cl-src-auto"]');
      expect(imposed.length).toBe(0);
      expect(auto.length).toBeGreaterThan(0);
    });
  });

  // ── 2. Catalog tab ───────────────────────────────────────────────────────
  describe("Catalog tab", () => {
    it("renders a table with one row per doc and selects the row on click", async () => {
      const docs: ApiDocument[] = [
        fixtureDoc({ id: "d-100", original_filename: "Alpha.pdf" }, 1),
        fixtureDoc({ id: "d-200", original_filename: "Beta.docx" }, 3),
      ];
      vi.spyOn(globalThis, "fetch").mockImplementation(
        makeFetchStub({ catalog: { items: docs, next_cursor: null } }),
      );

      render(<App />);

      // Wait for the explorer to settle into live mode.
      await waitFor(() => {
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      // Switch to the Catalog tab.
      const tab = screen.getByRole("tab", { name: /Catalog/ });
      fireEvent.click(tab);

      // Table renders both rows.
      const table = await screen.findByTestId("kx-catalog-table");
      const rows = table.querySelectorAll("tbody tr");
      expect(rows.length).toBe(2);

      // Click Beta — the DetailPanel should now show the doc title.
      const beta = table.querySelector('[data-doc-id="d-200"]') as HTMLElement | null;
      expect(beta).not.toBeNull();
      fireEvent.click(beta!);

      await waitFor(() => {
        // Beta has 3 versions → "v3" + "(3 versions)" surface in the
        // DetailPanel header.
        const detail = document.querySelector(".kx-detail-title");
        expect(detail?.textContent).toMatch(/Beta/);
        expect(detail?.textContent).toMatch(/v3/);
        expect(detail?.textContent).toMatch(/3 versions/);
      });
    });

    it("filtering by status narrows the visible rows", async () => {
      // Two docs — one VALIDATED, one REJECTED — plus a stub that
      // re-routes the second listDocuments call (with ?status=
      // present) to a smaller payload. The hook re-fetches when the
      // filter chip changes.
      const validated = fixtureDoc({ id: "d-v", original_filename: "Validated.pdf" }, 1);
      validated.versions[0].status = "VALIDATED";
      const rejected = fixtureDoc({ id: "d-r", original_filename: "Rejected.pdf" }, 1);
      rejected.versions[0].status = "REJECTED";

      vi.spyOn(globalThis, "fetch").mockImplementation(
        async (input: RequestInfo | URL): Promise<Response> => {
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
            // Filtered fetch: the chip sends ?status=VALIDATED
            if (url.includes("status=VALIDATED")) {
              return makeJson({ items: [validated], next_cursor: null });
            }
            if (url.includes("status=REJECTED")) {
              return makeJson({ items: [rejected], next_cursor: null });
            }
            return makeJson({ items: [validated, rejected], next_cursor: null });
          }
          if (url.includes("/knowledge/graph")) {
            return makeJson({ schema_version: "v0.2", nodes: [], edges: [], next_cursor: null });
          }
          return new Response("{}", { status: 404 });
        },
      );

      render(<App />);
      await waitFor(() => {
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
      const table = await screen.findByTestId("kx-catalog-table");
      // Both rows initially.
      await waitFor(() => {
        expect(table.querySelectorAll("tbody tr").length).toBe(2);
      });

      // Click the "Validated" filter chip — only Validated.pdf
      // should remain.
      const chip = screen.getByRole("tab", { name: "Validated" });
      fireEvent.click(chip);
      await waitFor(() => {
        const ids = Array.from(
          document
            .querySelector('[data-testid="kx-catalog-table"]')!
            .querySelectorAll("tbody tr"),
        ).map((r) => r.getAttribute("data-doc-id"));
        expect(ids).toEqual(["d-v"]);
      });
    });
  });

  // ── 3. Version count + Latest badge ──────────────────────────────────────
  describe("Document version count + Latest badge", () => {
    it("renders v3 + (3 versions) for a doc fixture with 3 versions in the catalog", async () => {
      const doc = fixtureDoc({ id: "doc-multi", original_filename: "Hybrid Work.pdf" }, 3);
      vi.spyOn(globalThis, "fetch").mockImplementation(
        makeFetchStub({ catalog: { items: [doc], next_cursor: null } }),
      );

      render(<App />);
      await waitFor(() => {
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      // Switch to Catalog so we can read the row's badges.
      fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
      const table = await screen.findByTestId("kx-catalog-table");
      const row = table.querySelector('[data-doc-id="doc-multi"]') as HTMLElement | null;
      expect(row).not.toBeNull();
      expect(row!.textContent).toMatch(/v3/);
      expect(row!.textContent).toMatch(/3 versions/);

      // Click the row → DetailPanel surfaces the Versions section
      // with one row per version.
      fireEvent.click(row!);
      await waitFor(() => {
        const section = document.querySelector('[data-testid="kx-versions-section"]');
        expect(section).not.toBeNull();
        const versionRows = section!.querySelectorAll("[data-version-number]");
        expect(versionRows.length).toBe(3);
        // Sorted descending — first row is v3.
        expect(versionRows[0].getAttribute("data-version-number")).toBe("3");
      });
    });

    it("hides the (N versions) text when the doc has only one version", async () => {
      const doc = fixtureDoc({ id: "doc-single", original_filename: "Single.pdf" }, 1);
      vi.spyOn(globalThis, "fetch").mockImplementation(
        makeFetchStub({ catalog: { items: [doc], next_cursor: null } }),
      );

      render(<App />);
      await waitFor(() => {
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      fireEvent.click(screen.getByRole("tab", { name: /Catalog/ }));
      const table = await screen.findByTestId("kx-catalog-table");
      const row = table.querySelector('[data-doc-id="doc-single"]') as HTMLElement | null;
      expect(row).not.toBeNull();
      // v1 badge present, but no "N versions" text.
      expect(row!.textContent).toMatch(/v1/);
      expect(row!.textContent).not.toMatch(/versions/);
    });
  });
});
