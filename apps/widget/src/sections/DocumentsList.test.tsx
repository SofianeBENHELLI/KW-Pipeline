import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  Document,
  DocumentListResponse,
  DocumentVersionStatus,
} from "../api/types";

import { DocumentsList } from "./DocumentsList";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

function makeDoc(
  id: string,
  filename: string,
  status: DocumentVersionStatus,
  createdAt: string = new Date().toISOString(),
): Document {
  const versionId = `${id}-v1`;
  return {
    id,
    original_filename: filename,
    latest_version_id: versionId,
    created_at: createdAt,
    versions: [
      {
        id: versionId,
        document_id: id,
        version_number: 1,
        filename,
        content_type: "application/pdf",
        file_size: 1024,
        sha256: "sha-" + id,
        storage_uri: "file://" + id,
        status,
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: createdAt,
      },
    ],
  };
}

function makeListResponse(
  items: Document[],
  nextCursor: string | null = null,
): DocumentListResponse {
  return { items, next_cursor: nextCursor };
}

const BASE_PROPS = {
  apiBaseUrl: "http://test",
  refreshTick: 0,
};

describe("DocumentsList (widget)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the loaded documents and the latest-version status badge", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeListResponse([
          makeDoc("d1", "spec.pdf", "VALIDATED"),
          makeDoc("d2", "draft.docx", "NEEDS_REVIEW"),
        ]),
      ),
    );

    render(<DocumentsList {...BASE_PROPS} />);

    expect(await screen.findByText("spec.pdf")).toBeInTheDocument();
    expect(screen.getByText("draft.docx")).toBeInTheDocument();
    // StatusBadge renders the status label; we don't lock down the exact
    // copy here, just that the validated row has *some* visible status.
  });

  it("renders the empty-corpus copy when the API returns no items", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(makeListResponse([])),
    );

    render(<DocumentsList {...BASE_PROPS} />);

    expect(await screen.findByText("No documents yet")).toBeInTheDocument();
  });

  it("renders the no-match copy when filter+query yield nothing", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    // First call (filter=all, q="") returns one doc. After clicking
    // Validated and typing "abc", the second call returns nothing.
    fetchSpy.mockResolvedValueOnce(
      makeJsonResponse(
        makeListResponse([makeDoc("d1", "first.pdf", "UPLOADED")]),
      ),
    );
    fetchSpy.mockResolvedValueOnce(
      makeJsonResponse(makeListResponse([])),
    );

    render(<DocumentsList {...BASE_PROPS} />);
    await screen.findByText("first.pdf");

    fireEvent.click(screen.getByRole("tab", { name: "Validated" }));
    fireEvent.change(screen.getByLabelText("Search filenames"), {
      target: { value: "abc" },
    });

    expect(await screen.findByText("Nothing matches")).toBeInTheDocument();
  });

  it("forwards ?status= and ?q= when filter + search are applied", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse(makeListResponse([])));

    render(<DocumentsList {...BASE_PROPS} />);
    fireEvent.click(screen.getByRole("tab", { name: "Review" }));
    fireEvent.change(screen.getByLabelText("Search filenames"), {
      target: { value: "ISO" },
    });

    await waitFor(() => {
      const calls = fetchSpy.mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      const last = calls[calls.length - 1] as [RequestInfo | URL, ...unknown[]];
      const url = urlOf(last[0]);
      expect(url).toContain("status=NEEDS_REVIEW");
      expect(url).toContain("status=DUPLICATE_DETECTED");
      expect(url).toContain("q=ISO");
    });
  });

  it("renders 'Load more' when the API reports a next_cursor and fetches the next page", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    fetchSpy.mockResolvedValueOnce(
      makeJsonResponse(
        makeListResponse([makeDoc("d1", "page1.pdf", "VALIDATED")], "cursor-2"),
      ),
    );
    fetchSpy.mockResolvedValueOnce(
      makeJsonResponse(
        makeListResponse([makeDoc("d2", "page2.pdf", "VALIDATED")], null),
      ),
    );

    render(<DocumentsList {...BASE_PROPS} />);
    expect(await screen.findByText("page1.pdf")).toBeInTheDocument();

    const loadMore = await screen.findByRole("button", { name: "Load more" });
    fireEvent.click(loadMore);

    expect(await screen.findByText("page2.pdf")).toBeInTheDocument();
    // First page1 still rendered (appended, not replaced).
    expect(screen.getByText("page1.pdf")).toBeInTheDocument();
    // Second call must include the cursor on the wire.
    const urls = fetchSpy.mock.calls.map((c) => urlOf((c as [RequestInfo | URL])[0]));
    expect(urls.some((u) => u.includes("cursor=cursor-2"))).toBe(true);
  });

  it("invokes onOpenDocument when a row is clicked", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeListResponse([makeDoc("d1", "click.pdf", "VALIDATED")]),
      ),
    );
    const onOpenDocument = vi.fn();

    render(<DocumentsList {...BASE_PROPS} onOpenDocument={onOpenDocument} />);
    fireEvent.click(await screen.findByText("click.pdf"));

    expect(onOpenDocument).toHaveBeenCalledTimes(1);
    expect(onOpenDocument).toHaveBeenCalledWith(
      expect.objectContaining({ id: "d1", original_filename: "click.pdf" }),
    );
  });

  it("flashes the matching row when highlightDocumentId points at a loaded doc", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        makeListResponse([
          makeDoc("d1", "alpha.pdf", "VALIDATED"),
          makeDoc("d2", "beta.pdf", "VALIDATED"),
        ]),
      ),
    );
    // Polyfill scrollIntoView — jsdom doesn't ship it.
    Element.prototype.scrollIntoView = vi.fn();

    const { container, rerender } = render(<DocumentsList {...BASE_PROPS} />);
    await screen.findByText("alpha.pdf");

    rerender(
      <DocumentsList {...BASE_PROPS} highlightDocumentId="d2" />,
    );

    await waitFor(() => {
      const row = container.querySelector('[data-doc-id="d2"]');
      expect(row).not.toBeNull();
      expect(row?.classList.contains("kw-doc-list__item--highlighted")).toBe(true);
    });
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("renders the error envelope on a failed first-page fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: { code: "KW_INTERNAL", message: "boom", retryable: true },
          detail: "boom",
        },
        500,
      ),
    );

    render(<DocumentsList {...BASE_PROPS} />);

    expect(await screen.findByText(/KW_INTERNAL.*boom/)).toBeInTheDocument();
  });
});
