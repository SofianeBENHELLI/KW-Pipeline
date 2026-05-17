/**
 * SimilarDocumentsModal tests (EPIC-C C.3, ADR-025 §3).
 *
 * Pins:
 *   * fetch → table renders with the spec'd cells (filename,
 *     percent-formatted confidence, [Open]),
 *   * [Open] dispatches onSelectDocument(neighbour) + closes,
 *   * empty results → spec'd "no similar" hint,
 *   * 403/404 → onError + close,
 *   * confidence formatting helper rounds to whole percents.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SimilarDocumentsModal,
  formatConfidence,
} from "./SimilarDocumentsModal";
import type { ApiSimilarDocument, ApiSimilarDocuments } from "../../api/types";

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeSimilar(results: ApiSimilarDocument[]): ApiSimilarDocuments {
  return { document_id: "doc-001", results };
}

describe("formatConfidence", () => {
  it("renders a ratio in [0, 1] as a whole percent", () => {
    expect(formatConfidence(0)).toBe("0%");
    expect(formatConfidence(0.5)).toBe("50%");
    expect(formatConfidence(0.875)).toBe("88%");
    expect(formatConfidence(1)).toBe("100%");
  });

  it("clamps out-of-band inputs into [0, 1]", () => {
    expect(formatConfidence(-0.1)).toBe("0%");
    expect(formatConfidence(1.4)).toBe("100%");
  });
});

describe("SimilarDocumentsModal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches and renders the ranked rows with percent-formatted confidence", async () => {
    const payload = makeSimilar([
      {
        document_id: "doc-neighbour-1",
        family_filename: "neighbour-1.txt",
        latest_version_status: "VALIDATED",
        similarity: 0.87,
      },
      {
        document_id: "doc-neighbour-2",
        family_filename: "neighbour-2.txt",
        latest_version_status: "NEEDS_REVIEW",
        similarity: 0.412,
      },
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = urlOf(input);
      if (url.includes("/similar")) {
        return Promise.resolve(makeJsonResponse(payload));
      }
      return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
    });

    render(
      <SimilarDocumentsModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("similar-table")).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId("similar-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("neighbour-1.txt");
    const confidences = screen.getAllByTestId("similar-confidence");
    expect(confidences[0]).toHaveTextContent("87%");
    expect(confidences[1]).toHaveTextContent("41%");
  });

  it("[Open] dispatches onSelectDocument(neighbour) and closes", async () => {
    const payload = makeSimilar([
      {
        document_id: "doc-neighbour-1",
        family_filename: "neighbour-1.txt",
        latest_version_status: "VALIDATED",
        similarity: 0.9,
      },
    ]);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(payload));

    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(
      <SimilarDocumentsModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={onClose}
        onSelectDocument={onSelect}
      />,
    );

    const openBtn = await screen.findByTestId("similar-open");
    fireEvent.click(openBtn);

    expect(onSelect).toHaveBeenCalledWith("doc-neighbour-1");
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the empty-state hint when results is empty", async () => {
    const payload = makeSimilar([]);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(payload));

    render(
      <SimilarDocumentsModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("similar-empty")).toHaveTextContent(
        /no similar documents found/i,
      );
    });
  });

  it("403 → onError + close", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Forbidden" }, 403),
    );

    const onClose = vi.fn();
    const onError = vi.fn();
    render(
      <SimilarDocumentsModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={onClose}
        onError={onError}
      />,
    );

    await waitFor(() => {
      expect(onError).toHaveBeenCalled();
    });
    expect(onClose).toHaveBeenCalled();
  });
});
