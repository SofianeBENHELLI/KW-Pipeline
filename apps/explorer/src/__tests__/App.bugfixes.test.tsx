/**
 * Regression tests for the three explorer bugs reported on 2026-05-04.
 *
 *   - Bug A — every cluster row in the left column toggles reliably
 *     (the previous auto-init re-added the first cluster on every
 *     ``expandedClusters.size === 0`` transition, making "People & HR"
 *     look stuck-on).
 *   - Bug B — chunk ↔ text cross-highlight round-trip (panel → viewer
 *     and viewer → panel both surface a visible active class on the
 *     matching row / paragraph).
 *   - Bug C — the side-panel show/hide control lives on the main
 *     toolbar (``data-testid="kx-toggle-side-panel"``) instead of
 *     buried inside the Tweaks gear menu.
 *
 * The tests render ``<App />`` against the sample fallback corpus by
 * mocking ``fetch`` to return an empty document list — that is the
 * code path ``useExplorerData`` takes when the backend is unreachable
 * or empty, and it gives us a stable, deterministic snapshot
 * (``SAMPLE_SNAPSHOT``) to assert against.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { CLUSTERS } from "../state/explorer-data";

describe("Knowledge Explorer — bug fixes (2026-05-04)", () => {
  beforeEach(() => {
    // Rejecting fetch pushes ``useExplorerData`` into ``sample-fallback``
    // mode (network unreachable), which still renders the deterministic
    // SAMPLE_SNAPSHOT every cluster-rail / detail-panel assertion in
    // this file relies on. Returning ``items: []`` is no longer
    // equivalent — that path now resolves to the real "empty corpus"
    // empty-state with zero clusters and zero docs.
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    // ``Element.prototype.scrollIntoView`` and ``scrollTo`` are not
    // implemented in jsdom; stub so DetailPanel's and DocViewer's
    // effects don't throw. (Visual scroll behaviour is verified
    // manually via ``npm run start``.)
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.scrollTo = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Bug A ────────────────────────────────────────────────────────────────
  describe("Bug A — cluster rows in the left column toggle reliably", () => {
    it("every sample cluster row activates on click and the first row can also be deactivated", async () => {
      render(<App />);

      // Wait for the sample-fallback snapshot to render. The HR
      // cluster row is keyed off CLUSTERS["hr"].label === "People & HR";
      // the same label is also rendered in the GraphCanvas SVG so we
      // tolerate multiple matches and confirm the cluster-list row is
      // among them.
      await waitFor(() => {
        const matches = screen.getAllByText("People & HR");
        expect(matches.length).toBeGreaterThan(0);
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      // The sample corpus has at least 4 clusters that the user
      // explicitly mentioned. We assert each one toggles into
      // ``kx-on`` on click. ``hr`` is the documented seed cluster so
      // it starts already active — we still expect a click to TOGGLE
      // it OFF (which is the regression: prior behaviour re-added it
      // on the next render). Searches are scoped to the cluster-list
      // because the GraphCanvas SVG also renders cluster labels.
      const clusterList = document.querySelector(".kx-cluster-list") as HTMLElement;
      expect(clusterList).not.toBeNull();
      const labels = ["People & HR", "Product", "Engineering", "Legal & Risk"];
      for (const label of labels) {
        const nameEl = Array.from(clusterList.querySelectorAll(".kx-cl-name")).find(
          (el) => el.textContent === label,
        );
        expect(nameEl, `cluster row missing for "${label}"`).toBeDefined();
        const row = nameEl!.closest(".kx-cl-row");
        expect(row, `cluster row missing for "${label}"`).not.toBeNull();

        const wasActive = row!.classList.contains("kx-on");
        fireEvent.click(row!);

        // After a click, the row's active state must have flipped.
        // Without the fix, "People & HR" stays kx-on because the
        // auto-init re-adds it as soon as ``expandedClusters`` empties.
        if (wasActive) {
          await waitFor(() => {
            expect(row!.classList.contains("kx-on")).toBe(false);
          });
        } else {
          await waitFor(() => {
            expect(row!.classList.contains("kx-on")).toBe(true);
          });
        }
      }
    });

    it("expanding a cluster narrows the document list to docs in that cluster", async () => {
      render(<App />);
      await waitFor(() => {
        // "People & HR" is rendered both in the cluster list and in
        // the GraphCanvas SVG, so tolerate multiple matches and just
        // confirm the cluster list is mounted.
        expect(screen.getAllByText("People & HR").length).toBeGreaterThan(0);
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      // The HR row starts expanded by the seed effect; expand
      // "Product" too and confirm at least one product-cluster doc
      // shows up directly underneath. Scope to the cluster-list
      // because the GraphCanvas SVG also renders the same labels.
      const clusterList = document.querySelector(".kx-cluster-list") as HTMLElement;
      const productNameEl = Array.from(clusterList.querySelectorAll(".kx-cl-name")).find(
        (el) => el.textContent === "Product",
      );
      const productRow = productNameEl!.closest(".kx-cl-row");
      expect(productRow).not.toBeNull();
      if (!productRow!.classList.contains("kx-on")) {
        fireEvent.click(productRow!);
      }
      await waitFor(() => {
        expect(productRow!.classList.contains("kx-on")).toBe(true);
      });

      // Atlas — PRD: Federated Search is doc d4 in SAMPLE_DOCUMENTS,
      // cluster: "product". Once Product is expanded its row must be
      // visible under that cluster block. The truncate helper caps
      // the title at 22 chars so we match the visible prefix.
      const productBlock = productRow!.closest(".kx-cl-block")!;
      await waitFor(() => {
        expect(productBlock.textContent).toMatch(/Atlas/);
      });
      // And — no HR documents leak under the Product block.
      expect(productBlock.textContent).not.toMatch(/Hybrid Work/);
    });
  });

  // ── Bug B ────────────────────────────────────────────────────────────────
  describe("Bug B — chunk ↔ text cross-highlight", () => {
    it("clicking a chunk row in the doc-detail panel highlights the matching paragraph in the viewer", async () => {
      render(<App />);
      await waitFor(() => {
        // "People & HR" is rendered both in the cluster list and in
        // the GraphCanvas SVG, so tolerate multiple matches and just
        // confirm the cluster list is mounted.
        expect(screen.getAllByText("People & HR").length).toBeGreaterThan(0);
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      // Open d1 (Hybrid Work Policy) by clicking its row in the HR
      // cluster. The HR cluster is auto-expanded by the seed effect.
      // We scope to the cluster list because the doc title also
      // appears in the viewer header (the seed effect opens d1 by
      // default), which would make ``findByText`` throw on multiple
      // matches.
      const clusterList = await waitFor(() => {
        const list = document.querySelector(".kx-cluster-list");
        expect(list).not.toBeNull();
        return list as HTMLElement;
      });
      const docRow = await waitFor(() => {
        const row = clusterList.querySelector(".kx-cl-doc");
        expect(row).not.toBeNull();
        return row as HTMLElement;
      });
      fireEvent.click(docRow);

      // The doc-detail panel renders a CHUNKS section with one row
      // per chunk in chunksForDoc(d1). Click c1.2 ("Remote work
      // eligibility").
      const chunksList = await screen.findByTestId("kx-doc-chunks");
      const c1_2 = chunksList.querySelector('[data-chunk-id="c1.2"]') as HTMLElement | null;
      expect(c1_2).not.toBeNull();
      fireEvent.click(c1_2!);

      // The DocViewer paragraph anchored to c1.2 (page 7, paras 0/1/2)
      // must now carry the kx-hl class. We pick para 0 — the first
      // anchored paragraph — and check its class list.
      await waitFor(() => {
        const para = document.querySelector('.kx-para[data-chunk-id="c1.2"]');
        expect(para).not.toBeNull();
        expect(para!.classList.contains("kx-hl")).toBe(true);
      });

      // And the panel row reflects the same active state.
      await waitFor(() => {
        expect(c1_2!.classList.contains("kx-on")).toBe(true);
      });
    });

    it("clicking a paragraph in the viewer surfaces the matching chunk row as active in the panel", async () => {
      render(<App />);
      await waitFor(() => {
        // "People & HR" is rendered both in the cluster list and in
        // the GraphCanvas SVG, so tolerate multiple matches and just
        // confirm the cluster list is mounted.
        expect(screen.getAllByText("People & HR").length).toBeGreaterThan(0);
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      // Open d1 so the viewer renders the SAMPLE_DOC_CONTENT["d1"]
      // pages. The default selection is null, so the doc detail
      // panel only renders once we click the doc. We pick the first
      // doc row in the (auto-expanded) HR cluster — that's d1.
      const clusterList = await waitFor(() => {
        const list = document.querySelector(".kx-cluster-list");
        expect(list).not.toBeNull();
        return list as HTMLElement;
      });
      const docRow = await waitFor(() => {
        const row = clusterList.querySelector(".kx-cl-doc");
        expect(row).not.toBeNull();
        return row as HTMLElement;
      });
      fireEvent.click(docRow);

      await screen.findByTestId("kx-doc-chunks");

      // The c1.3 anchor sits on page 11, paragraph 0. Click that
      // paragraph in the viewer.
      const para = document.querySelector('.kx-para[data-chunk-id="c1.3"]') as HTMLElement | null;
      expect(para).not.toBeNull();
      // ``kx-para-link`` is the affordance class our fix adds to
      // anchored paragraphs — confirms the paragraph is wired.
      expect(para!.classList.contains("kx-para-link")).toBe(true);
      fireEvent.click(para!);

      // The matching panel row now carries kx-on / aria-selected so
      // the user can see which chunk was just highlighted.
      const chunksList = screen.getByTestId("kx-doc-chunks");
      const c1_3 = chunksList.querySelector('[data-chunk-id="c1.3"]') as HTMLElement | null;
      expect(c1_3).not.toBeNull();
      await waitFor(() => {
        expect(c1_3!.classList.contains("kx-on")).toBe(true);
        expect(c1_3!.getAttribute("aria-selected")).toBe("true");
      });

      // Visual scroll behaviour (.kx-para → smooth scrollTo on the
      // viewer body, panel row → scrollIntoView) is exercised by the
      // effect; the ``scrollIntoView`` mock above asserts the call
      // happened. We don't assert pixel positions because jsdom
      // doesn't lay the page out — the visual behaviour is verified
      // manually via ``npm run start``.
      expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    });
  });

  // ── Bug C ────────────────────────────────────────────────────────────────
  describe("Bug C — side-panel toggle is on the main toolbar", () => {
    it("the toolbar button toggles the side panel and the Tweaks overlay no longer carries it", async () => {
      render(<App />);
      await waitFor(() => {
        // "People & HR" is rendered both in the cluster list and in
        // the GraphCanvas SVG, so tolerate multiple matches and just
        // confirm the cluster list is mounted.
        expect(screen.getAllByText("People & HR").length).toBeGreaterThan(0);
        expect(document.querySelector(".kx-cluster-list")).not.toBeNull();
      });

      const toggle = screen.getByTestId("kx-toggle-side-panel");
      expect(toggle.getAttribute("aria-pressed")).toBe("true");

      // Side panel mounted → kx-right aside present.
      expect(document.querySelector(".kx-right")).not.toBeNull();

      // Click the toolbar button → panel unmounts and aria-pressed flips.
      fireEvent.click(toggle);
      await waitFor(() => {
        expect(document.querySelector(".kx-right")).toBeNull();
        expect(toggle.getAttribute("aria-pressed")).toBe("false");
      });

      // Click again → panel comes back.
      fireEvent.click(toggle);
      await waitFor(() => {
        expect(document.querySelector(".kx-right")).not.toBeNull();
      });

      // Open Tweaks overlay — "Viewer panel" must NOT appear there
      // (the relocated control is the toolbar button above).
      const tweaksBtn = screen.getByLabelText("Tweaks");
      act(() => {
        fireEvent.click(tweaksBtn);
      });
      const dialog = await screen.findByRole("dialog", { name: "Tweaks" });
      expect(dialog.textContent).not.toMatch(/Viewer panel/);
      // Sanity check: the overlay still hosts the other tweaks.
      expect(dialog.textContent).toMatch(/Confidence heatmap/);
      expect(dialog.textContent).toMatch(/Cluster halos/);
    });
  });

  // Sanity — confirm the cluster registry still has the canonical
  // labels the panel renders. If somebody renames a sample cluster
  // key without updating CLUSTERS, the test above will fail loudly,
  // but this assertion narrows the diagnosis.
  it("CLUSTERS registry contains every label the bug-A test expects", () => {
    expect(CLUSTERS.hr.label).toBe("People & HR");
    expect(CLUSTERS.product.label).toBe("Product");
    expect(CLUSTERS.eng.label).toBe("Engineering");
    expect(CLUSTERS.legal.label).toBe("Legal & Risk");
  });
});
