/**
 * Component tests for ``SemanticAssetList`` (#408).
 *
 * Pins:
 *   1. Empty state.
 *   2. Each row renders type chip + text + confidence + status pill.
 *   3. Sort by descending confidence.
 *   4. Long lists cap at ``initialCount`` and surface +N more.
 *   5. Status pill className matches the asset's review_status.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ApiSemanticAsset, ReviewStatus } from "../../api/types";
import { SemanticAssetList } from "./SemanticAssetList";

function makeAsset(
  i: number,
  overrides: Partial<ApiSemanticAsset> = {},
): ApiSemanticAsset {
  return {
    id: `a-${i}`,
    type: "claim",
    text: `Asset text ${i}`,
    confidence: 0.5,
    review_status: "needs_review" as ReviewStatus,
    source_reference_ids: [],
    ...overrides,
  };
}

describe("SemanticAssetList", () => {
  it("renders the explicit empty-state copy when given no assets", () => {
    render(<SemanticAssetList assets={[]} />);
    expect(screen.getByTestId("sem-assets-empty")).toHaveTextContent(
      /No assets extracted/i,
    );
  });

  it("renders type, text, confidence, and status for each asset", () => {
    const assets = [
      makeAsset(0, { type: "requirement", confidence: 0.92, review_status: "validated" }),
    ];
    render(<SemanticAssetList assets={assets} />);
    const row = screen.getByTestId("sem-asset-row");
    expect(within(row).getByTestId("sem-asset-type")).toHaveTextContent("requirement");
    expect(within(row).getByTestId("sem-asset-text")).toHaveTextContent("Asset text 0");
    expect(within(row).getByTestId("sem-asset-confidence")).toHaveTextContent("92%");
    expect(within(row).getByTestId("sem-asset-status")).toHaveTextContent("validated");
  });

  it("sorts rows by descending confidence", () => {
    const assets = [
      makeAsset(0, { confidence: 0.4 }),
      makeAsset(1, { confidence: 0.9 }),
      makeAsset(2, { confidence: 0.65 }),
    ];
    render(<SemanticAssetList assets={assets} />);
    const confidences = screen
      .getAllByTestId("sem-asset-confidence")
      .map((n) => n.textContent);
    expect(confidences).toEqual(["90%", "65%", "40%"]);
  });

  it("caps long lists at initialCount and shows the +N more affordance", () => {
    const assets = Array.from({ length: 15 }, (_, i) =>
      makeAsset(i, { confidence: i / 15 }),
    );
    render(<SemanticAssetList assets={assets} initialCount={5} />);
    expect(screen.getAllByTestId("sem-asset-row")).toHaveLength(5);
    expect(screen.getByTestId("sem-assets-more")).toHaveTextContent("+10 more");
    fireEvent.click(screen.getByTestId("sem-assets-more"));
    expect(screen.getAllByTestId("sem-asset-row")).toHaveLength(15);
    expect(screen.queryByTestId("sem-assets-more")).toBeNull();
  });

  it("status pill className reflects the review_status", () => {
    const statuses: ReviewStatus[] = [
      "needs_review",
      "source_backed",
      "validated",
      "rejected",
    ];
    const assets = statuses.map((s, i) => makeAsset(i, { review_status: s }));
    render(<SemanticAssetList assets={assets} />);
    const pills = screen.getAllByTestId("sem-asset-status");
    expect(pills).toHaveLength(statuses.length);
    for (const status of statuses) {
      const pill = pills.find((p) =>
        p.className.includes(`sem-asset__status--${status}`),
      );
      expect(pill).toBeTruthy();
    }
  });

  it("renders source-reference IDs when present", () => {
    const assets = [makeAsset(0, { source_reference_ids: ["ref-x", "ref-y"] })];
    render(<SemanticAssetList assets={assets} />);
    const refs = screen.getByTestId("sem-asset-refs");
    expect(refs).toHaveTextContent("ref-x");
    expect(refs).toHaveTextContent("ref-y");
  });
});
