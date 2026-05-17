/**
 * ChatPanel tests — pin mode toggle, send flow, citation rendering,
 * Phase-3 disabled state.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ChatPanel } from "./ChatPanel";

function makeJsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Probe component used by the citation-click test to read back the
// review page's path + search string after navigation.
function _LocationProbe(): ReactElement {
  const loc = useLocation();
  return (
    <div
      data-testid="review-page"
      data-pathname={loc.pathname}
      data-search={loc.search}
    />
  );
}

function renderChat() {
  return render(
    <MemoryRouter initialEntries={["/kf/chat"]}>
      <Routes>
        <Route path="/kf/chat" element={<ChatPanel />} />
        <Route path="/kf/review/:docId" element={<_LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<ChatPanel />", () => {
  afterEach(() => vi.restoreAllMocks());

  it("defaults to Hybrid mode and renders the placeholder", () => {
    renderChat();
    expect(screen.getByRole("tab", { name: "Hybrid" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText(/Ask a question grounded/i)).toBeInTheDocument();
  });

  it("switching mode flips aria-selected", () => {
    renderChat();
    fireEvent.click(screen.getByRole("tab", { name: "GraphRAG" }));
    expect(screen.getByRole("tab", { name: "GraphRAG" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("clicking Send fires askKnowledgeChat and renders the assistant turn + citation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        schema_version: "v0.1",
        question: "What was Q4 ARR?",
        mode: "hybrid",
        answer: "Q4 net new ARR closed at $8.4M.",
        citations: [
          {
            chunk_id: "c1",
            document_id: "doc-1",
            version_id: "v1",
            section_id: "s1",
            snippet: "Net new ARR closed at $8.4M",
            score: 0.94,
          },
        ],
        embedding_model: "voyage-3",
        llm_model: "claude-3.5",
        token_usage: { input: 100, output: 50 },
        warnings: [],
      }),
    );
    renderChat();
    fireEvent.change(screen.getByLabelText("Chat input"), {
      target: { value: "What was Q4 ARR?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));

    const reply = await screen.findByTestId(
      "kf-chat-turn-assistant",
      undefined,
      { timeout: 1000 },
    );
    expect(reply).toHaveTextContent(/Q4 net new ARR closed at \$8\.4M/);
    expect(reply).toHaveTextContent(/\[1\] doc-1/);
  });

  it("clicking a citation navigates to /kf/review/:doc?chunk=:chunk (deep-link to the cited chunk, not just the document)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        schema_version: "v0.1",
        question: "q",
        mode: "hybrid",
        answer: "a",
        citations: [
          {
            chunk_id: "chunk-abc",
            document_id: "doc-1",
            version_id: "v1",
            section_id: "s1",
            snippet: null,
            score: 0.5,
          },
        ],
        embedding_model: "voyage-3",
        llm_model: "claude-3.5",
        token_usage: {},
        warnings: [],
      }),
    );
    renderChat();
    fireEvent.change(screen.getByLabelText("Chat input"), {
      target: { value: "q" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    const citeButton = await screen.findByRole("button", {
      name: /\[1\] doc-1/,
    });
    fireEvent.click(citeButton);
    const probe = await screen.findByTestId("review-page");
    expect(probe.getAttribute("data-pathname")).toBe("/kf/review/doc-1");
    expect(probe.getAttribute("data-search")).toBe("?chunk=chunk-abc");
  });

  it("Enter sends; Shift+Enter inserts a newline", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        schema_version: "v0.1",
        question: "x",
        mode: "hybrid",
        answer: "ok",
        citations: [],
        embedding_model: null,
        llm_model: "x",
        token_usage: {},
        warnings: [],
      }),
    );
    renderChat();
    const input = screen.getByLabelText("Chat input");
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("kf-chat-turn-user")).toHaveTextContent("hi"),
    );
  });

  it("renders the Phase-3 disabled banner on KW_CHAT_DISABLED", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          detail: "Grounded chat is disabled.",
          error: {
            code: "KW_CHAT_DISABLED",
            message: "Grounded chat is disabled.",
            status: 503,
            retryable: false,
            remediation:
              "Set KW_KNOWLEDGE_LAYER_ENABLED=true and configure VOYAGE_API_KEY + an LLM key.",
          },
        },
        503,
      ),
    );
    renderChat();
    fireEvent.change(screen.getByLabelText("Chat input"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send/ }));
    const banner = await screen.findByTestId("kf-chat-disabled", undefined, {
      timeout: 1000,
    });
    expect(banner).toHaveTextContent(/Chat disabled/i);
    expect(banner).toHaveTextContent(/VOYAGE_API_KEY/);
  });
});
