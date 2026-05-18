/**
 * Explorer MVP tests (slice 7).
 *
 * Pin the primary fetch path for each route, the empty-state hints,
 * and the 403 collapse for the atlas. Search-as-you-type uses a
 * 200ms debounce so we drive vitest fake timers for the topics view.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExploreView } from "./ExploreView";

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

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/kf/explore/*" element={<ExploreView />} />
      </Routes>
    </MemoryRouter>,
  );
}

const ATLAS_PAYLOAD = {
  schema_version: "v0.1",
  top_topics: [
    {
      topic_id: "t-1",
      label: "Battery thermal",
      keywords: ["thermal", "cell"],
      document_count: 4,
      chunk_count: 12,
    },
    {
      topic_id: "t-2",
      label: "",
      keywords: ["hr"],
      document_count: 1,
      chunk_count: 2,
    },
  ],
  validation_coverage: {
    total_documents: 5,
    validated_count: 3,
    needs_review_count: 1,
    rejected_count: 0,
    other_count: 1,
  },
  recent_documents: [],
  bridge_documents: [],
  outlier_relations: [],
};

describe("<ExploreView /> — Atlas (/kf/explore)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the 3 metric cards + top-10 topics list", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(ATLAS_PAYLOAD),
    );
    renderAt("/kf/explore");
    await screen.findByTestId("kf-explore-atlas-cards");
    expect(screen.getByText("Total documents")).toBeInTheDocument();
    expect(screen.getByText("Validated")).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByTestId("kf-explore-atlas-topic-t-1")).toHaveTextContent(
      "Battery thermal",
    );
    // Topic without a label falls back to the id.
    expect(screen.getByTestId("kf-explore-atlas-topic-t-2")).toHaveTextContent(
      "t-2",
    );
  });

  it("renders the empty-topics hint when top_topics is []", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse({ ...ATLAS_PAYLOAD, top_topics: [] }),
    );
    renderAt("/kf/explore");
    expect(
      await screen.findByTestId("kf-explore-atlas-empty-topics"),
    ).toBeInTheDocument();
  });

  it("403 envelope collapses the page to Forbidden", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      makeJsonResponse(
        {
          error: {
            code: "KW_FORBIDDEN",
            message: "Admin role required.",
            status: 403,
            retryable: false,
          },
          detail: "Admin role required.",
        },
        403,
      ),
    );
    renderAt("/kf/explore");
    expect(await screen.findByText("Forbidden")).toBeInTheDocument();
  });
});

describe("<ExploreView /> — Topics list (/kf/explore/topics)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the topics index and routes search through /knowledge/explore/search", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) => {
        const url = urlOf(input);
        if (url.includes("/knowledge/topics")) {
          return makeJsonResponse({
            schema_version: "v0.1",
            items: [
              {
                id: "t-100",
                document_id: "doc-1",
                version_id: "v-1",
                label: "Renewals",
                summary: "renewals theme",
                keywords: ["renewal", "churn"],
                confidence: 0.9,
                supporting_chunk_ids: ["c-1"],
                extracted_at: "2026-05-01T00:00:00Z",
              },
            ],
            next_cursor: null,
          });
        }
        if (url.includes("/knowledge/explore/search")) {
          return makeJsonResponse({
            schema_version: "v0.1",
            query: "ren",
            embedding_model: "voyage-3",
            documents: [],
            chunks: [],
            topics: [
              {
                topic_id: "t-500",
                label: "Renewal slip",
                keywords: ["renewal"],
                score: 0.9,
                evidence_chunks: [],
              },
            ],
            entities: [],
            relations: [],
          });
        }
        return makeJsonResponse({}, 404);
      },
    );
    renderAt("/kf/explore/topics");
    await screen.findByTestId("kf-explore-topic-row-t-100");
    expect(screen.getByText("Renewals")).toBeInTheDocument();

    // Type into the search; wait for debounced explore call.
    fireEvent.change(screen.getByTestId("kf-explore-topic-search"), {
      target: { value: "ren" },
    });
    await waitFor(() => {
      const calls = fetchSpy.mock.calls.map(([input]) => urlOf(input));
      expect(calls.some((u) => u.includes("/knowledge/explore/search"))).toBe(
        true,
      );
    });
    // Search results swap in for the unfiltered list.
    expect(
      await screen.findByTestId("kf-explore-topic-row-t-500"),
    ).toBeInTheDocument();
  });
});

describe("<ExploreView /> — Topic detail (/kf/explore/topics/:id)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the focused lens + citations panel", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) => {
        const url = urlOf(input);
        if (url.includes("/knowledge/neighborhood")) {
          return makeJsonResponse({
            depth: 2,
            root_id: "t-1",
            root_kind: "topic",
            schema_version: "v0.1",
            hidden_edge_count: 0,
            hidden_node_count: 0,
            truncated: false,
            nodes: [
              {
                id: "t-1",
                kind: "topic",
                label: "Battery thermal",
                properties: {},
              },
              {
                id: "c-1",
                kind: "chunk",
                label: "chunk-1",
                properties: {},
              },
            ],
            edges: [
              {
                id: "e-1",
                kind: "belongs_to",
                source_id: "c-1",
                target_id: "t-1",
                properties: {},
                is_bridge: false,
                is_outlier: false,
                score: 0.7,
                strength_class: "medium",
              },
            ],
          });
        }
        if (url.includes("/knowledge/explore/search")) {
          return makeJsonResponse({
            schema_version: "v0.1",
            query: "t-1",
            embedding_model: "voyage-3",
            documents: [],
            chunks: [
              {
                chunk_id: "c-1",
                document_id: "doc-1",
                section_id: "s-1",
                score: 0.9,
                snippet: "Battery cooling threshold exceeded.",
                is_source_backed: true,
              },
            ],
            topics: [],
            entities: [],
            relations: [],
          });
        }
        return makeJsonResponse({}, 404);
      },
    );
    renderAt("/kf/explore/topics/t-1");
    await screen.findByTestId("kf-explore-topic-lens");
    expect(
      await screen.findByTestId("kf-explore-citation-c-1"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Battery cooling threshold/)).toBeInTheDocument();
  });

  it("renders the empty-lens hint when nodes is []", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = urlOf(input);
      if (url.includes("/knowledge/neighborhood")) {
        return makeJsonResponse({
          depth: 2,
          root_id: "t-1",
          root_kind: "topic",
          schema_version: "v0.1",
          nodes: [],
          edges: [],
          hidden_edge_count: 0,
          hidden_node_count: 0,
          truncated: false,
        });
      }
      return makeJsonResponse({
        schema_version: "v0.1",
        query: "t-1",
        embedding_model: "",
        documents: [],
        chunks: [],
        topics: [],
        entities: [],
        relations: [],
      });
    });
    renderAt("/kf/explore/topics/t-1");
    expect(
      await screen.findByTestId("kf-explore-topic-empty"),
    ).toBeInTheDocument();
  });
});
