/**
 * Widget App integration test.
 *
 * Covers the cross-section navigation feature: when the user clicks a
 * citation in the chat panel or a result in the search panel, App.tsx
 * switches to the docs mode and highlights the matching document row
 * via DocumentsList's ``highlightDocumentId`` prop.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeDoc(id: string, filename: string) {
  const versionId = `${id}-v1`;
  const created = new Date().toISOString();
  return {
    id,
    original_filename: filename,
    latest_version_id: versionId,
    created_at: created,
    versions: [
      {
        id: versionId,
        document_id: id,
        version_number: 1,
        filename,
        content_type: "application/pdf",
        file_size: 1024,
        sha256: `sha-${id}`,
        storage_uri: `file://${id}`,
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: created,
      },
    ],
  };
}

// Routes fetch calls to canned responses based on URL pattern.
function makeRouter(routes: { match: RegExp; body: unknown; status?: number }[]) {
  return (input: RequestInfo | URL): Promise<Response> => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    for (const r of routes) {
      if (r.match.test(url)) {
        return Promise.resolve(makeJsonResponse(r.body, r.status));
      }
    }
    return Promise.reject(new Error(`Unrouted fetch: ${url}`));
  };
}

const HEALTH_BODY = { status: "ok", version: "1.0.0" };
const DOC_LIST_BODY = {
  items: [makeDoc("doc-A", "alpha.pdf"), makeDoc("doc-B", "beta.pdf")],
  next_cursor: null,
};
const CHAT_BODY = {
  schema_version: "v0.1",
  question: "?",
  mode: "rag",
  answer: "See [chunk-1].",
  citations: [
    {
      chunk_id: "chunk-1",
      document_id: "doc-B",
      version_id: "doc-B-v1",
      section_id: "sec-1",
      snippet: "snippet",
      score: 0.9,
    },
  ],
  embedding_model: "fake",
  llm_model: "claude-test",
  token_usage: { input_tokens: 1, output_tokens: 1 },
  warnings: [],
};

describe("App (widget) — cross-section navigation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("clicking a chat citation switches to docs mode and flashes the cited row", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeRouter([
        { match: /\/health/, body: HEALTH_BODY },
        { match: /\/documents/, body: DOC_LIST_BODY },
        { match: /\/knowledge\/chat/, body: CHAT_BODY },
      ]),
    );

    const { container } = render(<App />);

    // Default mode is ``docs`` — both rows render.
    expect(await screen.findByText("alpha.pdf")).toBeInTheDocument();

    // Switch to chat mode via the side rail.
    fireEvent.click(screen.getByRole("button", { name: "Knowledge chat" }));

    // Submit a question in the chat panel.
    fireEvent.change(screen.getByTestId("chat-panel-input"), {
      target: { value: "anything" },
    });
    fireEvent.click(screen.getByTestId("chat-panel-submit"));

    // Click the citation once it lands.
    await screen.findByTestId("chat-panel-answer");
    const citationButtons = screen
      .getAllByTestId("chat-panel-citation")
      .map((node) => node.querySelector("button"))
      .filter((node): node is HTMLButtonElement => node !== null);
    expect(citationButtons.length).toBeGreaterThan(0);
    fireEvent.click(citationButtons[0]);

    // Mode flipped back to ``docs`` and the cited row got the
    // highlight class.
    await waitFor(() => {
      expect(screen.getByText("beta.pdf")).toBeInTheDocument();
    });
    await waitFor(() => {
      const row = container.querySelector('[data-doc-id="doc-B"]');
      expect(row?.classList.contains("kw-doc-list__item--highlighted")).toBe(true);
    });
  });

  it("clicking a search result also jumps to the cited document", async () => {
    Element.prototype.scrollIntoView = vi.fn();
    vi.spyOn(globalThis, "fetch").mockImplementation(
      makeRouter([
        { match: /\/health/, body: HEALTH_BODY },
        { match: /\/documents/, body: DOC_LIST_BODY },
        {
          match: /\/knowledge\/search/,
          body: {
            schema_version: "v0.1",
            query: "alpha",
            embedding_model: "fake",
            query_embedding_dim: 16,
            results: [
              {
                chunk_id: "chunk-1",
                document_id: "doc-A",
                version_id: "doc-A-v1",
                section_id: "sec-1",
                snippet: "alpha snippet",
                score: 0.95,
              },
            ],
          },
        },
      ]),
    );

    const { container } = render(<App />);
    await screen.findByText("alpha.pdf");

    fireEvent.click(screen.getByRole("button", { name: "Knowledge search" }));

    fireEvent.change(screen.getByTestId("search-panel-input"), {
      target: { value: "alpha" },
    });

    await screen.findByTestId("search-panel-results");
    const resultButton = screen
      .getByTestId("search-panel-result")
      .querySelector("button");
    expect(resultButton).not.toBeNull();
    fireEvent.click(resultButton as HTMLButtonElement);

    await waitFor(() => {
      const row = container.querySelector('[data-doc-id="doc-A"]');
      expect(row?.classList.contains("kw-doc-list__item--highlighted")).toBe(true);
    });
  });
});
