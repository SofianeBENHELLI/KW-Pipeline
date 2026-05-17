/**
 * Coverage for the Knowledge Explorer Topic Detail view (ADR-028).
 *
 * Pinned scenarios:
 *   - Header renders the topic label / keywords pulled from
 *     /knowledge/topics.
 *   - Focused lens fetches /knowledge/neighborhood with depth=2 —
 *     critical per ADR-028 §"Information Architecture" §3, the lens
 *     never reads the full corpus graph.
 *   - Citations list renders chunks from /knowledge/explore/search.
 *   - 403 KW_FORBIDDEN collapses to the Forbidden state.
 *   - Empty-state hint copy when no chunks come back.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ApiKnowledgeExploreSearch,
  ApiKnowledgeNeighborhood,
  ApiKnowledgeTopic,
  ApiKnowledgeTopicsList,
} from "../../../api/types";
import { TopicDetailView } from "../TopicDetailView";

// The NVL canvas is heavy and not useful under jsdom — stub it so the
// test asserts only on our wiring around it.
vi.mock("@neo4j-nvl/react", () => ({
  InteractiveNvlWrapper: (props: { nodes: unknown[]; rels: unknown[] }) => (
    <div
      data-testid="nvl-stub"
      data-node-count={props.nodes.length}
      data-rel-count={props.rels.length}
    />
  ),
}));

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
    label: "Audit",
    summary: "Audit summary",
    keywords: ["audit", "supplier"],
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

function makeNeighborhood(
  overrides: Partial<ApiKnowledgeNeighborhood> = {},
): ApiKnowledgeNeighborhood {
  return {
    schema_version: "v0.1",
    root_kind: "topic",
    root_id: "topic-1",
    depth: 2,
    nodes: [
      {
        id: "topic-1",
        kind: "topic",
        label: "Audit",
        properties: {},
      },
    ],
    edges: [],
    hidden_node_count: 0,
    hidden_edge_count: 0,
    truncated: false,
    ...overrides,
  };
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

function renderView(topicId = "topic-1") {
  return render(
    <MemoryRouter
      initialEntries={[`/kf/explore/topics/${encodeURIComponent(topicId)}`]}
    >
      <Routes>
        <Route
          path="/kf/explore/topics/:topicId"
          element={<TopicDetailView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopicDetailView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the topic header from /knowledge/topics", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(
            makeJsonResponse(makeTopicsList([makeTopic()])),
          );
        }
        if (url.includes("/knowledge/neighborhood")) {
          return Promise.resolve(makeJsonResponse(makeNeighborhood()));
        }
        if (url.includes("/knowledge/explore/search")) {
          return Promise.resolve(makeJsonResponse(makeSearchResponse()));
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(screen.getByTestId("topic-detail-label").textContent).toBe(
        "Audit",
      );
    });
    expect(screen.getByTestId("topic-detail-keywords").textContent).toContain(
      "audit",
    );
  });

  it("fetches the focused lens at depth=2 — bounded neighborhood, not full corpus", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation((input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(
            makeJsonResponse(makeTopicsList([makeTopic()])),
          );
        }
        if (url.includes("/knowledge/neighborhood")) {
          return Promise.resolve(makeJsonResponse(makeNeighborhood()));
        }
        if (url.includes("/knowledge/explore/search")) {
          return Promise.resolve(makeJsonResponse(makeSearchResponse()));
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      });

    renderView();

    await waitFor(() => {
      expect(screen.getByTestId("focused-lens")).toBeInTheDocument();
    });

    const neighborhoodCall = fetchSpy.mock.calls
      .map((c) => urlOf(c[0] as RequestInfo | URL))
      .find((u) => u.includes("/knowledge/neighborhood"))!;
    expect(neighborhoodCall).toContain("root_kind=topic");
    expect(neighborhoodCall).toContain("root_id=topic-1");
    expect(neighborhoodCall).toContain("depth=2");

    // ADR-028 §"Information Architecture" §3 — never the full corpus.
    expect(
      fetchSpy.mock.calls
        .map((c) => urlOf(c[0] as RequestInfo | URL))
        .find((u) => /\/knowledge\/graph($|\?)/.test(u)),
    ).toBeUndefined();
  });

  it("renders citations from /knowledge/explore/search", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(
            makeJsonResponse(makeTopicsList([makeTopic()])),
          );
        }
        if (url.includes("/knowledge/neighborhood")) {
          return Promise.resolve(makeJsonResponse(makeNeighborhood()));
        }
        if (url.includes("/knowledge/explore/search")) {
          return Promise.resolve(
            makeJsonResponse(
              makeSearchResponse({
                chunks: [
                  {
                    chunk_id: "chunk-1",
                    document_id: "doc-1",
                    version_id: "ver-1",
                    section_id: "sec-1",
                    score: 0.91,
                    snippet: "Audit programme detail",
                    validation_status: null,
                    is_source_backed: false,
                  },
                ],
              }),
            ),
          );
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(
        screen.getByTestId("topic-detail-citations-list"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("topic-detail-citation-chunk-1").getAttribute("href"),
    ).toBe("/kf/review/doc-1?chunk=chunk-1");
  });

  it("renders Forbidden on a 403 envelope from /knowledge/topics", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(
            makeJsonResponse(
              {
                error: { code: "KW_FORBIDDEN", message: "Access denied" },
              },
              403,
            ),
          );
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Forbidden" }),
      ).toBeInTheDocument();
    });
  });

  it("renders the empty-citations hint when no chunks come back", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(
            makeJsonResponse(makeTopicsList([makeTopic()])),
          );
        }
        if (url.includes("/knowledge/neighborhood")) {
          return Promise.resolve(makeJsonResponse(makeNeighborhood()));
        }
        if (url.includes("/knowledge/explore/search")) {
          return Promise.resolve(makeJsonResponse(makeSearchResponse()));
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(
        screen.getByTestId("topic-detail-citations-empty"),
      ).toBeInTheDocument();
    });
  });
});
