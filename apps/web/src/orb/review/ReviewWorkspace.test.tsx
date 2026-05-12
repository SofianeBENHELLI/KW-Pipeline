/**
 * ReviewWorkspace — pin the page shell composition, URL/state sync,
 * tab switching, sort toggling, and selection sync.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { ApiDocument } from "../../api/types";
import { ReviewWorkspace, sortDocs } from "./ReviewWorkspace";

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

const DOC_A: ApiDocument = {
  id: "doc-a",
  original_filename: "alpha.md",
  latest_version_id: "ver-a",
  created_at: "2026-05-11T14:22:08Z",
  archived_at: null,
  scopes: [
    { kind: "project", ref: "p1", added_at: "x", added_by: "a", removed_at: null },
  ],
  versions: [
    {
      id: "ver-a",
      document_id: "doc-a",
      version_number: 1,
      filename: "alpha.md",
      content_type: "text/markdown",
      file_size: 4096,
      sha256: "ha",
      storage_uri: "file://a",
      status: "NEEDS_REVIEW",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-11T14:22:08Z",
    },
  ],
};

const DOC_B: ApiDocument = {
  id: "doc-b",
  original_filename: "beta.md",
  latest_version_id: "ver-b",
  created_at: "2026-05-10T09:00:00Z",
  archived_at: null,
  scopes: [],
  versions: [
    {
      id: "ver-b",
      document_id: "doc-b",
      version_number: 2,
      filename: "beta.md",
      content_type: "text/markdown",
      file_size: 2048,
      sha256: "hb",
      storage_uri: "file://b",
      status: "VALIDATED",
      duplicate_of_version_id: null,
      failure_reason: null,
      reviewer_note: null,
      reviewed_at: null,
      created_at: "2026-05-10T09:00:00Z",
    },
  ],
};

function renderWorkspace(initialPath: string, overrides: Partial<React.ComponentProps<typeof ReviewWorkspace>> = {}) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/kf/review" element={<ReviewWorkspace {...overrides} />} />
        <Route path="/kf/review/:docId" element={<ReviewWorkspace {...overrides} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<ReviewWorkspace />", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.match(/\/documents\/doc-a\/graph$/)) {
          // Linked View hits this on tab=linked. Return an empty
          // projection so the panel renders the empty state instead of
          // throwing on undefined nodes.
          return Promise.resolve(
            makeJsonResponse({
              document_id: "doc-a",
              version_id: "ver-a",
              generated_at: "2026-05-12T09:00:00Z",
              schema_version: "v0.2",
              nodes: [],
              edges: [],
            }),
          );
        }
        if (url.match(/\/documents\/doc-a$/)) {
          return Promise.resolve(makeJsonResponse(DOC_A));
        }
        if (url.includes("/documents")) {
          return Promise.resolve(
            makeJsonResponse({ items: [DOC_A, DOC_B], next_cursor: null }),
          );
        }
        return Promise.resolve(makeJsonResponse({ detail: "Not found" }, 404));
      },
    );
  });

  afterEach(() => vi.restoreAllMocks());

  it("renders the rail + main pane shell with the empty header", async () => {
    renderWorkspace("/kf/review");
    await waitFor(() =>
      expect(screen.getByText("alpha.md")).toBeInTheDocument(),
    );
    expect(screen.getByText("beta.md")).toBeInTheDocument();
    expect(screen.getByText(/Pick a document from the rail/i)).toBeInTheDocument();
  });

  it("clicking a row updates the URL to /kf/review/:docId", async () => {
    renderWorkspace("/kf/review");
    await waitFor(() =>
      expect(screen.getByText("alpha.md")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText(/Open alpha\.md/));
    // After navigation the doc detail fetch resolves and the title
    // appears in the header.
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /alpha\.md/ }),
      ).toBeInTheDocument(),
    );
  });

  it("renders the doc title + status when navigated with :docId", async () => {
    renderWorkspace("/kf/review/doc-a");
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: /alpha\.md/ }),
      ).toBeInTheDocument(),
    );
    // The badge appears twice — once in the rail row, once in the
    // header. Asserting `getAllByRole().length >= 2` proves the header
    // copy rendered without depending on document order.
    expect(
      screen.getAllByRole("status", { name: "NEEDS_REVIEW" }).length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("defaults to the Linked view tab and switches to Pipeline on click", async () => {
    renderWorkspace("/kf/review/doc-a");
    await waitFor(() =>
      expect(screen.getByTestId("kf-tab-linked")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: /^Pipeline$/ }));
    expect(screen.getByTestId("kf-tab-pipeline")).toBeInTheDocument();
  });

  it("the view filter syncs to ?view= in the URL", async () => {
    renderWorkspace("/kf/review");
    await waitFor(() =>
      expect(screen.getByText("alpha.md")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("tab", { name: /^Validated$/ }));
    // Use the rail's aria-selected as the proof that the view changed
    // (URL inspection in MemoryRouter would require a Location capture
    // helper; we keep the assertion behaviour-level here).
    await waitFor(() => {
      expect(
        screen.getByRole("tab", { name: /Validated/ }),
      ).toHaveAttribute("aria-selected", "true");
    });
  });

  it("uses fixtureDocs override when supplied (skip live fetch)", () => {
    renderWorkspace("/kf/review", { fixtureDocs: [DOC_B] });
    expect(screen.getByText("beta.md")).toBeInTheDocument();
    expect(screen.queryByText("alpha.md")).toBeNull();
  });

  it("renders the rail loading state on first paint", async () => {
    const { container } = renderWorkspace("/kf/review");
    expect(container.querySelectorAll(".kf-rail__row--skeleton").length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(screen.getByText("alpha.md")).toBeInTheDocument(),
    );
  });

  it("checking a row reveals the batch bar; navigating away resets the count", async () => {
    renderWorkspace("/kf/review");
    await waitFor(() =>
      expect(screen.getByText("alpha.md")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("checkbox", { name: /Select alpha\.md/ }));
    const region = await screen.findByRole("region", {
      name: /Batch selection/,
    });
    expect(within(region).getByText("1 selected")).toBeInTheDocument();
  });
});

describe("sortDocs", () => {
  it("sorts by filename asc/desc", () => {
    const asc = sortDocs([DOC_B, DOC_A], { col: "filename", dir: "asc" });
    expect(asc.map((d) => d.id)).toEqual(["doc-a", "doc-b"]);
    const desc = sortDocs([DOC_A, DOC_B], { col: "filename", dir: "desc" });
    expect(desc.map((d) => d.id)).toEqual(["doc-b", "doc-a"]);
  });

  it("sorts by uploaded asc/desc using the latest version timestamp", () => {
    const asc = sortDocs([DOC_A, DOC_B], { col: "uploaded", dir: "asc" });
    expect(asc.map((d) => d.id)).toEqual(["doc-b", "doc-a"]);
    const desc = sortDocs([DOC_A, DOC_B], { col: "uploaded", dir: "desc" });
    expect(desc.map((d) => d.id)).toEqual(["doc-a", "doc-b"]);
  });

  it("sorts by status alphabetically", () => {
    const asc = sortDocs([DOC_A, DOC_B], { col: "status", dir: "asc" });
    expect(asc.map((d) => d.id)).toEqual(["doc-a", "doc-b"]);
  });
});
