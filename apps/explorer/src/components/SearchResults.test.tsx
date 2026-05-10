/**
 * Component-side tests for ``SearchResults``. The four data-flavoured
 * states (idle/loading/empty/error/disabled) plus the populated state
 * are each exercised; click-to-pick fires the right hit envelope; the
 * validated-only toggle filters chunks/docs.
 *
 * The hook itself is tested separately in ``use-explore-search.test.ts``.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SearchResults } from "./SearchResults";
import type { ExploreSearchSnapshot } from "../state/use-explore-search";
import type { ExploreSearchResponse } from "../api/types";

function snapshot(
  partial: Partial<ExploreSearchSnapshot> & Pick<ExploreSearchSnapshot, "state">,
): ExploreSearchSnapshot {
  return {
    query: partial.query ?? "test",
    response: partial.response ?? null,
    error: partial.error ?? null,
    ...partial,
  };
}

const POPULATED: ExploreSearchResponse = {
  schema_version: "v0.1",
  query: "policy",
  embedding_model: "voyage-3",
  chunks: [
    {
      chunk_id: "c-1",
      document_id: "d-1",
      version_id: "v-1",
      section_id: "s-1",
      snippet: "Reviewer must validate every claim.",
      score: 0.91,
      validation_status: null,
      is_source_backed: true,
    },
  ],
  documents: [
    {
      document_id: "d-1",
      title: "Supplier policy",
      score: 0.94,
      validation_status: "VALIDATED",
      is_source_backed: false,
      contributing_chunks: [],
    },
    {
      document_id: "d-2",
      title: "Draft note",
      score: 0.63,
      validation_status: null,
      is_source_backed: false,
      contributing_chunks: [],
    },
  ],
  topics: [
    {
      topic_id: "t-1",
      label: "Compliance",
      keywords: ["audit", "review"],
      score: 0.81,
      evidence_chunks: [],
    },
  ],
  entities: [],
  relations: [],
};

describe("SearchResults", () => {
  it("renders nothing when state is idle", () => {
    const { container } = render(
      <SearchResults
        snapshot={snapshot({ state: "idle", query: "" })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the loading affordance while debounce / fetch is in flight", () => {
    render(
      <SearchResults
        snapshot={snapshot({ state: "loading" })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    expect(screen.getByTestId("kx-search-loading")).toHaveTextContent(/Searching/);
  });

  it("renders the disabled callout when Phase 3 is off", () => {
    render(
      <SearchResults
        snapshot={snapshot({ state: "disabled" })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    expect(screen.getByTestId("kx-search-disabled")).toBeInTheDocument();
    expect(screen.getByText(/Vector search is disabled/i)).toBeInTheDocument();
  });

  it("renders the error banner with the message preserved", () => {
    render(
      <SearchResults
        snapshot={snapshot({ state: "error", error: "Boom" })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    expect(screen.getByTestId("kx-search-error")).toHaveTextContent("Boom");
  });

  it("renders 'no matches' for the empty-corpus / no-hit response", () => {
    render(
      <SearchResults
        snapshot={snapshot({
          state: "empty",
          query: "noresults",
          response: { ...POPULATED, chunks: [], documents: [], topics: [] },
        })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    expect(screen.getByTestId("kx-search-empty")).toHaveTextContent("noresults");
  });

  it("renders all three populated groups with embedding-model meta", () => {
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: POPULATED })}
        validatedOnly={false}
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    // All three sections present.
    expect(screen.getByTestId("kx-search-section-documents")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-section-chunks")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-section-topics")).toBeInTheDocument();
    // Toolbar shows the embedding model + the toggle.
    expect(screen.getByText("voyage-3")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-validated-toggle")).toBeInTheDocument();
  });

  it("validated-only toggle hides candidate documents and chunks", () => {
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: POPULATED })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    // Validated doc remains.
    expect(screen.getByText("Supplier policy")).toBeInTheDocument();
    // Candidate doc is filtered out.
    expect(screen.queryByText("Draft note")).not.toBeInTheDocument();
    // The source-backed chunk remains (is_source_backed === true).
    expect(
      screen.getByText("Reviewer must validate every claim."),
    ).toBeInTheDocument();
  });

  it("'no validated matches' surfaces when the filter zeroes out every group", () => {
    const onlyCandidates: ExploreSearchResponse = {
      ...POPULATED,
      documents: [
        {
          document_id: "d-2",
          title: "Draft note",
          score: 0.4,
          validation_status: null,
          is_source_backed: false,
          contributing_chunks: [],
        },
      ],
      chunks: [
        {
          chunk_id: "c-2",
          document_id: "d-2",
          version_id: "v-2",
          section_id: "s-1",
          snippet: "Draft text.",
          score: 0.42,
          validation_status: null,
          is_source_backed: false,
        },
      ],
      topics: [],
    };
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: onlyCandidates })}
        validatedOnly
        onToggleValidated={() => {}}
        onPick={() => {}}
      />,
    );
    expect(screen.getByTestId("kx-search-empty-after-filter")).toBeInTheDocument();
  });

  it("clicking a document hit invokes onPick with the document_id", () => {
    const onPick = vi.fn();
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: POPULATED })}
        validatedOnly={false}
        onToggleValidated={() => {}}
        onPick={onPick}
      />,
    );
    fireEvent.click(screen.getByText("Supplier policy"));
    expect(onPick).toHaveBeenCalledWith({
      kind: "doc",
      id: "d-1",
      documentId: "d-1",
    });
  });

  it("clicking a chunk hit invokes onPick with the chunk_id + parent document_id", () => {
    const onPick = vi.fn();
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: POPULATED })}
        validatedOnly={false}
        onToggleValidated={() => {}}
        onPick={onPick}
      />,
    );
    fireEvent.click(screen.getByText("Reviewer must validate every claim."));
    expect(onPick).toHaveBeenCalledWith({
      kind: "chunk",
      id: "c-1",
      documentId: "d-1",
    });
  });

  it("clicking a topic hit invokes onPick with the topic_id", () => {
    const onPick = vi.fn();
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: POPULATED })}
        validatedOnly={false}
        onToggleValidated={() => {}}
        onPick={onPick}
      />,
    );
    fireEvent.click(screen.getByText("Compliance"));
    expect(onPick).toHaveBeenCalledWith({ kind: "topic", id: "t-1" });
  });

  it("toggle invokes onToggleValidated with the new value", () => {
    const onToggle = vi.fn();
    render(
      <SearchResults
        snapshot={snapshot({ state: "data", response: POPULATED })}
        validatedOnly
        onToggleValidated={onToggle}
        onPick={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("kx-search-validated-toggle"));
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
