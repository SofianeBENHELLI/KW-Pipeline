/**
 * Coverage for the Knowledge Explorer atlas landing (ADR-028, #316).
 *
 * Pinned scenarios:
 *   - Atlas metric cards render the validation-coverage counts.
 *   - Top-10 topics list renders rows linking to /kf/explore/topics/:id.
 *   - 403 KW_FORBIDDEN collapses to the Forbidden state.
 *   - Empty topics list renders the empty hint.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ApiKnowledgeAtlas,
  ApiKnowledgeTopic,
  ApiKnowledgeTopicsList,
} from "../../../api/types";
import { ExploreLandingView } from "../ExploreLandingView";

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

function makeAtlas(
  overrides: Partial<ApiKnowledgeAtlas> = {},
): ApiKnowledgeAtlas {
  return {
    schema_version: "v0.1",
    top_topics: [],
    bridge_documents: [],
    outlier_relations: [],
    recent_documents: [],
    validation_coverage: {
      total_documents: 42,
      validated_count: 30,
      needs_review_count: 10,
      rejected_count: 1,
      other_count: 1,
    },
    ...overrides,
  };
}

function makeTopic(
  overrides: Partial<ApiKnowledgeTopic> = {},
): ApiKnowledgeTopic {
  return {
    id: "topic-1",
    document_id: "doc-1",
    version_id: "ver-1",
    label: "Supplier audit",
    summary: "Supplier audit programmes",
    keywords: ["audit", "supplier"],
    confidence: 0.9,
    supporting_chunk_ids: ["chunk-1"],
    extracted_at: "2026-05-01T00:00:00Z",
    schema_version: "v0.1",
    ...overrides,
  };
}

function makeTopicsList(
  items: ApiKnowledgeTopic[],
): ApiKnowledgeTopicsList {
  return {
    items,
    next_cursor: null,
    schema_version: "v0.1",
  };
}

function renderView() {
  return render(
    <MemoryRouter initialEntries={["/kf/explore"]}>
      <ExploreLandingView />
    </MemoryRouter>,
  );
}

describe("ExploreLandingView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders atlas validation-coverage metric cards", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/atlas")) {
          return Promise.resolve(makeJsonResponse(makeAtlas()));
        }
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(makeJsonResponse(makeTopicsList([])));
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(screen.getByTestId("atlas-metric-grid")).toBeInTheDocument();
    });

    expect(screen.getByTestId("atlas-total-documents").textContent).toBe(
      "42",
    );
    expect(screen.getByTestId("atlas-validated").textContent).toBe("30");
    expect(screen.getByTestId("atlas-needs-review").textContent).toBe("10");
  });

  it("renders top-10 topics with links to the detail view", async () => {
    const items = Array.from({ length: 10 }, (_, i) =>
      makeTopic({
        id: `topic-${i}`,
        label: `Topic ${i}`,
        keywords: ["alpha", "beta"],
      }),
    );

    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/atlas")) {
          return Promise.resolve(makeJsonResponse(makeAtlas()));
        }
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(makeJsonResponse(makeTopicsList(items)));
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(screen.getByTestId("atlas-topic-list")).toBeInTheDocument();
    });

    expect(screen.getAllByRole("link")).toHaveLength(10);
    const firstLink = screen.getByTestId("atlas-topic-link-topic-0");
    expect(firstLink.getAttribute("href")).toBe("/kf/explore/topics/topic-0");
  });

  it("renders Forbidden on a 403 envelope", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/atlas")) {
          return Promise.resolve(
            makeJsonResponse(
              {
                error: { code: "KW_FORBIDDEN", message: "Access denied" },
              },
              403,
            ),
          );
        }
        return Promise.resolve(makeJsonResponse(makeTopicsList([])));
      },
    );

    renderView();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Forbidden" })).toBeInTheDocument();
    });
  });

  it("renders the empty-topics hint when the corpus has no topics", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (input: RequestInfo | URL): Promise<Response> => {
        const url = urlOf(input);
        if (url.includes("/knowledge/atlas")) {
          return Promise.resolve(makeJsonResponse(makeAtlas()));
        }
        if (url.includes("/knowledge/topics")) {
          return Promise.resolve(makeJsonResponse(makeTopicsList([])));
        }
        return Promise.resolve(makeJsonResponse({}, 404));
      },
    );

    renderView();

    await waitFor(() => {
      expect(screen.getByTestId("atlas-empty-topics")).toBeInTheDocument();
    });
  });
});
