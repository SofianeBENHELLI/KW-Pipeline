import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiChatResponse } from "../../api/types";

import { ChatPanel } from "./ChatPanel";

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

const FIXTURE_RESPONSE: ApiChatResponse = {
  schema_version: "v0.1",
  question: "When is the audit?",
  mode: "rag",
  answer: "The audit is scheduled for Q3 [chunk-1].",
  citations: [
    {
      chunk_id: "chunk-1",
      document_id: "doc-A",
      version_id: "ver-A",
      section_id: "sec-1",
      snippet: "Q3 audit calendar entry.",
      score: 0.91,
    },
  ],
  embedding_model: "fake-embedding",
  llm_model: "claude-test",
  token_usage: { input_tokens: 10, output_tokens: 8 },
  warnings: [],
};

function submit(question: string) {
  fireEvent.change(screen.getByTestId("chat-panel-input"), {
    target: { value: question },
  });
  fireEvent.click(screen.getByTestId("chat-panel-submit"));
}

describe("ChatPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders an empty form by default and does not call the API", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<ChatPanel />);
    expect(screen.getByTestId("chat-panel")).toBeInTheDocument();
    expect(screen.getByTestId("chat-panel-input")).toHaveValue("");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("submits the question, renders the answer + citations", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(FIXTURE_RESPONSE));

    render(<ChatPanel />);
    submit("When is the audit?");

    const answer = await screen.findByTestId("chat-panel-answer");
    expect(answer).toHaveTextContent(/Q3 \[chunk-1\]/);
    const citations = screen.getAllByTestId("chat-panel-citation");
    expect(citations).toHaveLength(1);
    expect(screen.getByText("91.0%")).toBeInTheDocument();
    expect(screen.getByText(/Q3 audit calendar entry/)).toBeInTheDocument();
  });

  it("forwards the selected mode in the request body", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(makeJsonResponse({ ...FIXTURE_RESPONSE, mode: "hybrid" }));

    render(<ChatPanel />);
    fireEvent.click(screen.getByTestId("chat-mode-hybrid"));
    submit("question");

    await screen.findByTestId("chat-panel-answer");
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    // openapi-fetch invokes ``fetch(request)`` with a single ``Request``
    // argument; older signatures pass ``(url, init)``. Tolerate both.
    const [arg, maybeInit] = fetchSpy.mock.calls[0] as [
      RequestInfo | URL,
      RequestInit | undefined,
    ];
    let url = urlOf(arg);
    let bodyText: string | null = null;
    if (arg instanceof Request) {
      bodyText = await arg.clone().text();
    } else if (maybeInit?.body !== undefined) {
      bodyText =
        typeof maybeInit.body === "string"
          ? maybeInit.body
          : await new Response(maybeInit.body).text();
    }
    expect(url).toContain("/knowledge/chat");
    expect(bodyText).not.toBeNull();
    expect(JSON.parse(bodyText as string)).toEqual({
      question: "question",
      mode: "hybrid",
      top_k: 5,
    });
  });

  it("renders the disabled banner with remediation when API returns 503", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_CHAT_DISABLED",
            message: "Grounded chat is disabled.",
            status: 503,
            retryable: false,
            remediation:
              "Set KW_KNOWLEDGE_LAYER_ENABLED=true, configure at least one LLM key (GEMINI_API_KEY or ANTHROPIC_API_KEY), and provide VOYAGE_API_KEY.",
          },
          detail: "Grounded chat is disabled.",
        },
        503,
      ),
    );

    render(<ChatPanel />);
    submit("anything");

    const banner = await screen.findByTestId("chat-panel-disabled");
    expect(banner).toHaveTextContent("Grounded chat is disabled");
    expect(banner).toHaveTextContent(/GEMINI_API_KEY/);
    expect(banner).toHaveTextContent(/ANTHROPIC_API_KEY/);
    expect(banner).toHaveTextContent(/VOYAGE_API_KEY/);
    expect(screen.queryByTestId("chat-panel-error")).toBeNull();
    expect(screen.queryByTestId("chat-panel-answer")).toBeNull();
  });

  it("renders a generic error banner on non-503 failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ detail: "Internal" }, 500),
    );

    render(<ChatPanel />);
    submit("anything");

    const banner = await screen.findByTestId("chat-panel-error");
    expect(banner).toBeInTheDocument();
    expect(screen.queryByTestId("chat-panel-disabled")).toBeNull();
  });

  it("renders an empty-answer hint when the model returned no text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        ...FIXTURE_RESPONSE,
        answer: "",
        citations: [],
      }),
    );

    render(<ChatPanel />);
    submit("question");

    await screen.findByTestId("chat-panel-empty-answer");
    expect(screen.queryByTestId("chat-panel-citations")).toBeNull();
  });

  it("invokes onSelectCitation when a citation is clicked", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeJsonResponse(FIXTURE_RESPONSE));
    const onSelectCitation = vi.fn();

    render(<ChatPanel onSelectCitation={onSelectCitation} />);
    submit("anything");

    await screen.findByTestId("chat-panel-answer");
    const citationButtons = screen
      .getAllByTestId("chat-panel-citation")
      .map((node) => node.querySelector("button"))
      .filter((node): node is HTMLButtonElement => node !== null);
    fireEvent.click(citationButtons[0]);

    expect(onSelectCitation).toHaveBeenCalledTimes(1);
    expect(onSelectCitation).toHaveBeenCalledWith(
      expect.objectContaining({ chunk_id: "chunk-1", document_id: "doc-A" }),
    );
  });

  it("renders unresolved-citation warnings when the API returns them", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({
        ...FIXTURE_RESPONSE,
        answer: "Cited [c-fake] which doesn't exist.",
        warnings: ["[c-fake]", "[doc:doc-fake]"],
      }),
    );

    render(<ChatPanel />);
    submit("question");

    const warnings = await screen.findByTestId("chat-panel-warnings");
    expect(warnings).toHaveTextContent("Unresolved citations");
    expect(warnings).toHaveTextContent("[c-fake]");
    expect(warnings).toHaveTextContent("[doc:doc-fake]");
  });

  it("disables the submit button while a request is in flight", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    vi.spyOn(globalThis, "fetch").mockReturnValue(fetchPromise);

    render(<ChatPanel />);
    submit("question");
    expect(screen.getByTestId("chat-panel-submit")).toBeDisabled();

    resolveFetch(makeJsonResponse(FIXTURE_RESPONSE));
    await waitFor(() => {
      expect(screen.getByTestId("chat-panel-submit")).not.toBeDisabled();
    });
  });
});
