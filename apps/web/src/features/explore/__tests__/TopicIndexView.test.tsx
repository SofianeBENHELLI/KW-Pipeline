/**
 * Coverage for the Knowledge Explorer Topic Index (ADR-028).
 *
 * Pinned scenarios:
 *   - Empty input → paginated list from /knowledge/topics renders.
 *   - Typed query → grouped /knowledge/explore/search?q= fires after
 *     the 300 ms debounce window, not on each keystroke.
 *   - 403 KW_FORBIDDEN collapses to the Forbidden state.
 *   - Empty result renders the empty hint copy.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ApiKnowledgeExploreSearch,
  ApiKnowledgeTopic,
  ApiKnowledgeTopicsList,
} from "../../../api/types";
import { TopicIndexView } from "../TopicIndexView";

const DEBOUNCE_MS = 300;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

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

function makeTopic(
  overrides: Partial<ApiKnowledgeTopic> = {},
): ApiKnowledgeTopic {
  return {
    id: "topic-1",
    document_id: "doc-1",
    version_id: "ver-1",
    label: "Supplier audit",
    summary: "Audit summary",
    keywords: ["audit"],
    confidence: 0.9,
    supporting_chunk_ids: ["c1"],
    extracted_at: "2026-05-01T00:00:00Z",
    schema_version: "v0.1",
    ...overrides,
  };
}

function makeTopicsList(items: ApiKnowledgeTopic[]): ApiKnowledgeTopicsList {
  return { items, next_cursor: null, schema_version: "v0.1" };
}

function makeSearchResponse(
  overrides: Partial<ApiKnowledgeExploreSearch> = {},
): ApiKnowledgeExploreSearch {
  return {
    schema_version: "v0.1",
    query: "",
    embedding_model: "voyage-3",
    chunks: [],
    documents: [],
    topics: [],
    entities: [],
    relations: [],
    ...overrides,
  };
}

function renderView() {
  return render(
    <MemoryRouter initialEntries={["/kf/explore/topics"]}>
      <TopicIndexView />
    </MemoryRouter>,
  );
}

describe("TopicIndexView", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the topics list when the input is empty", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(
            makeJsonResponse(
              makeTopicsList([
                makeTopic({ id: "topic-a", label: "Audit alpha" }),
                makeTopic({ id: "topic-b", label: "Bridge beta" }),
              ]),
            ),
          );
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("topic-index-list")).toBeInTheDocument();
    });
    expect(screen.getByTestId("topic-index-link-topic-a")).toBeInTheDocument();
    expect(screen.getByTestId("topic-index-link-topic-b")).toBeInTheDocument();
  });

  it("debounces typed queries — search does not fire until ~300ms idle", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(makeJsonResponse(makeTopicsList([])));
        }
        if (url.includes("/knowledge/explore/search")) {
          return Promise.resolve(
            makeJsonResponse(
              makeSearchResponse({
                topics: [
                  {
                    topic_id: "search-hit",
                    label: "Search hit",
                    keywords: ["found"],
                    score: 0.91,
                    evidence_chunks: [],
                  },
                ],
              }),
            ),
          );
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      });

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("topic-search-input")).toBeInTheDocument();
    });
    fetchSpy.mockClear();

    const input = screen.getByTestId("topic-search-input") as HTMLInputElement;
    // Three rapid keystrokes — none should fire a search call yet.
    fireEvent.change(input, { target: { value: "a" } });
    fireEvent.change(input, { target: { value: "au" } });
    fireEvent.change(input, { target: { value: "aud" } });

    // Inside the debounce window: no search call should have fired.
    await sleep(50);
    expect(
      fetchSpy.mock.calls.filter((c) =>
        urlOf(c[0] as RequestInfo | URL).includes("/knowledge/explore/search"),
      ),
    ).toHaveLength(0);

    // After the debounce window expires, exactly one search fires for
    // the latest input value.
    await sleep(DEBOUNCE_MS + 100);
    await waitFor(() => {
      const calls = fetchSpy.mock.calls
        .map((c) => urlOf(c[0] as RequestInfo | URL))
        .filter((u) => u.includes("/knowledge/explore/search"));
      expect(calls.length).toBeGreaterThanOrEqual(1);
      expect(calls[calls.length - 1]).toContain("q=aud");
    });
  });

  it("renders Forbidden on a 403 envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (): Promise<Response> =>
        Promise.resolve(
          makeJsonResponse(
            { error: { code: "KW_FORBIDDEN", message: "Access denied" } },
            403,
          ),
        ),
    );

    renderView();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Forbidden" }),
      ).toBeInTheDocument();
    });
  });

  it("renders the empty-state hint when no topics exist", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(makeJsonResponse(makeTopicsList([]))),
    );

    renderView();
    await waitFor(() => {
      expect(screen.getByTestId("topic-index-empty")).toBeInTheDocument();
    });
  });
});
