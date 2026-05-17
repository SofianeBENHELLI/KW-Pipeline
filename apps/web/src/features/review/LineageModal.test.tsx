/**
 * LineageModal tests (EPIC-C C.3).
 *
 * Pins the fetch → list-renders → row-click contract plus the
 * spec'd empty / error states. The component-under-test stays loose
 * about navigation: it calls ``onSelectDocument`` with the family id
 * and closes itself, leaving the parent (ReviewWorkspace) to wire the
 * actual selection swap.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LineageModal } from "./LineageModal";
import type { ApiDocumentLineage, ApiLineageVersion } from "../../api/types";

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

function makeLineageVersion(
  versionNumber: number,
  overrides: Partial<ApiLineageVersion> = {},
): ApiLineageVersion {
  return {
    id: `ver-${versionNumber}`,
    filename: `spec-v${versionNumber}.txt`,
    version_number: versionNumber,
    file_size: 1000,
    sha256: "a".repeat(64),
    status: "VALIDATED",
    duplicate_of_version_id: null,
    ingested_at: `2026-05-0${versionNumber}T12:00:00Z`,
    is_latest: false,
    superseded_by_version_id: null,
    ...overrides,
  };
}

function makeLineage(versions: ApiLineageVersion[]): ApiDocumentLineage {
  return {
    document_id: "doc-001",
    family_filename: "spec.txt",
    versions,
  };
}

describe("LineageModal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches and renders the version chain on open", async () => {
    const lineage = makeLineage([
      makeLineageVersion(1, { status: "SUPERSEDED" }),
      makeLineageVersion(2, { is_latest: true }),
    ]);
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = urlOf(input);
      if (url.endsWith("/lineage")) {
        return Promise.resolve(makeJsonResponse(lineage));
      }
      return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
    });

    render(
      <LineageModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("lineage-list")).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId("lineage-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("spec-v1.txt");
    expect(rows[0]).toHaveTextContent("v1");
    expect(rows[0]).toHaveTextContent("2026-05-01");
    expect(rows[1]).toHaveTextContent("current");
  });

  it("calling a row → fires onSelectDocument and closes", async () => {
    const lineage = makeLineage([makeLineageVersion(1, { is_latest: true })]);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(lineage));

    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(
      <LineageModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={onClose}
        onSelectDocument={onSelect}
      />,
    );

    const row = await screen.findByTestId("lineage-row");
    fireEvent.click(row);

    expect(onSelect).toHaveBeenCalledWith("doc-001");
    expect(onClose).toHaveBeenCalled();
  });

  it("renders the empty-state hint when versions is empty", async () => {
    const lineage = makeLineage([]);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(lineage));

    render(
      <LineageModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("lineage-empty")).toHaveTextContent(
        /only version of this document/i,
      );
    });
  });

  it("surfaces 403 to onError + closes the modal", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Forbidden" }, 403),
    );

    const onClose = vi.fn();
    const onError = vi.fn();
    render(
      <LineageModal
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

  it("ESC closes the modal", async () => {
    const lineage = makeLineage([makeLineageVersion(1, { is_latest: true })]);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(lineage));

    const onClose = vi.fn();
    render(
      <LineageModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={onClose}
      />,
    );

    // Wait for fetch to settle so the initial render committed.
    await screen.findByTestId("lineage-row");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("backdrop click closes the modal but card click does not", async () => {
    const lineage = makeLineage([makeLineageVersion(1, { is_latest: true })]);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(lineage));

    const onClose = vi.fn();
    render(
      <LineageModal
        documentId="doc-001"
        filename="spec.txt"
        onClose={onClose}
      />,
    );
    await screen.findByTestId("lineage-row");

    const backdrop = screen.getByTestId("lineage-modal");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledTimes(1);

    onClose.mockReset();
    // Clicking the row content shouldn't fire a backdrop-close.
    fireEvent.click(screen.getByTestId("lineage-row"));
    // The row click fires onClose via handleSelect, so wait for it
    // then assert that only one close fired (the select path), not
    // the backdrop one — i.e. the click did not bubble through the
    // card to register as a backdrop click as well.
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
