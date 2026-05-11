/**
 * Component-side tests for ``SearchResults``. The four data-flavoured
 * states (idle/loading/empty/error/disabled) plus the populated state
 * are each exercised; click-to-pick fires the right hit envelope; the
 * validated-only toggle filters chunks/docs.
 *
 * The hook itself is tested separately in ``use-explore-search.test.ts``.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { SearchResults, type SearchResultsProps } from "./SearchResults";
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

/**
 * Tiny render helper — injects safe defaults for every prop the test
 * doesn't override. Keeps the per-test boilerplate down to the prop
 * actually under test, and means adding a future required prop only
 * touches one place.
 */
function renderResults(
  overrides: Partial<SearchResultsProps> &
    Pick<SearchResultsProps, "snapshot">,
): ReactElement {
  const props: SearchResultsProps = {
    snapshot: overrides.snapshot,
    validatedOnly: overrides.validatedOnly ?? false,
    onToggleValidated: overrides.onToggleValidated ?? (() => {}),
    scoreThreshold: overrides.scoreThreshold ?? 0,
    onChangeScoreThreshold: overrides.onChangeScoreThreshold ?? (() => {}),
    onPick: overrides.onPick ?? (() => {}),
  };
  return <SearchResults {...props} />;
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
      evidence_chunks: [
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
    },
  ],
  entities: [],
  relations: [],
};

describe("SearchResults", () => {
  it("renders nothing when state is idle", () => {
    const { container } = render(
      renderResults({ snapshot: snapshot({ state: "idle", query: "" }), validatedOnly: true }),
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the loading affordance while debounce / fetch is in flight", () => {
    render(renderResults({ snapshot: snapshot({ state: "loading" }), validatedOnly: true }));
    expect(screen.getByTestId("kx-search-loading")).toHaveTextContent(/Searching/);
  });

  it("renders the disabled callout when Phase 3 is off", () => {
    render(renderResults({ snapshot: snapshot({ state: "disabled" }), validatedOnly: true }));
    expect(screen.getByTestId("kx-search-disabled")).toBeInTheDocument();
    expect(screen.getByText(/Vector search is disabled/i)).toBeInTheDocument();
  });

  it("renders the error banner with the message preserved", () => {
    render(
      renderResults({
        snapshot: snapshot({ state: "error", error: "Boom" }),
        validatedOnly: true,
      }),
    );
    expect(screen.getByTestId("kx-search-error")).toHaveTextContent("Boom");
  });

  it("renders 'no matches' for the empty-corpus / no-hit response", () => {
    render(
      renderResults({
        snapshot: snapshot({
          state: "empty",
          query: "noresults",
          response: { ...POPULATED, chunks: [], documents: [], topics: [] },
        }),
        validatedOnly: true,
      }),
    );
    expect(screen.getByTestId("kx-search-empty")).toHaveTextContent("noresults");
  });

  it("renders all three populated groups with embedding-model meta", () => {
    render(renderResults({ snapshot: snapshot({ state: "data", response: POPULATED }) }));
    // All three sections present.
    expect(screen.getByTestId("kx-search-section-documents")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-section-chunks")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-section-topics")).toBeInTheDocument();
    // Toolbar shows the embedding model + the toggle + the threshold slider.
    expect(screen.getByText("voyage-3")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-validated-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("kx-search-threshold-slider")).toBeInTheDocument();
  });

  it("validated-only toggle hides candidate documents and chunks", () => {
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        validatedOnly: true,
      }),
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

  it("'no matches' surfaces when the trust filter zeroes out every group", () => {
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
      renderResults({
        snapshot: snapshot({ state: "data", response: onlyCandidates }),
        validatedOnly: true,
      }),
    );
    expect(screen.getByTestId("kx-search-empty-after-filter")).toBeInTheDocument();
  });

  it("score threshold hides rows below the floor across every group", () => {
    // POPULATED scores: doc d-1=0.94, doc d-2=0.63, chunk c-1=0.91, topic t-1=0.81.
    // A 0.80 floor keeps d-1 + c-1 + t-1, drops d-2.
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        scoreThreshold: 0.8,
      }),
    );
    expect(screen.getByText("Supplier policy")).toBeInTheDocument();
    expect(screen.queryByText("Draft note")).not.toBeInTheDocument();
    expect(
      screen.getByText("Reviewer must validate every claim."),
    ).toBeInTheDocument();
    expect(screen.getByText("Compliance")).toBeInTheDocument();
  });

  it("score threshold at 1.0 surfaces the after-filter empty state", () => {
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        scoreThreshold: 1.0,
      }),
    );
    expect(screen.getByTestId("kx-search-empty-after-filter")).toBeInTheDocument();
  });

  it("threshold slider invokes onChangeScoreThreshold with the parsed numeric value", () => {
    const onChange = vi.fn();
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        scoreThreshold: 0,
        onChangeScoreThreshold: onChange,
      }),
    );
    fireEvent.change(screen.getByTestId("kx-search-threshold-slider"), {
      target: { value: "0.5" },
    });
    expect(onChange).toHaveBeenCalledWith(0.5);
  });

  it("threshold value is rendered as a percentage in the toolbar", () => {
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        scoreThreshold: 0.42,
      }),
    );
    expect(screen.getByTestId("kx-search-threshold-value")).toHaveTextContent("42%");
  });

  it("clicking a document hit invokes onPick with the document_id", () => {
    const onPick = vi.fn();
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        onPick,
      }),
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
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        onPick,
      }),
    );
    fireEvent.click(screen.getByText("Reviewer must validate every claim."));
    expect(onPick).toHaveBeenCalledWith({
      kind: "chunk",
      id: "c-1",
      documentId: "d-1",
    });
  });

  it("clicking a topic hit invokes onPick with the strongest evidence chunk", () => {
    const onPick = vi.fn();
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        onPick,
      }),
    );
    fireEvent.click(screen.getByText("Compliance"));
    // The hit envelope carries the first evidence chunk's id +
    // parent document id so the parent can route the user to the
    // source paragraph in DocViewer rather than the previous no-op.
    expect(onPick).toHaveBeenCalledWith({
      kind: "topic",
      id: "t-1",
      chunkId: "c-1",
      documentId: "d-1",
    });
  });

  it("clicking a topic with no evidence still fires onPick (soft no-op envelope)", () => {
    // Topics surfaced via embedding similarity alone may have an
    // empty ``evidence_chunks`` list. The hit envelope omits
    // chunkId/documentId in that case, and the parent handler is
    // expected to clear the search and stop without crashing.
    const noEvidence: ExploreSearchResponse = {
      ...POPULATED,
      topics: [
        {
          topic_id: "t-bare",
          label: "Bare topic",
          keywords: [],
          score: 0.55,
          evidence_chunks: [],
        },
      ],
    };
    const onPick = vi.fn();
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: noEvidence }),
        onPick,
      }),
    );
    fireEvent.click(screen.getByText("Bare topic"));
    expect(onPick).toHaveBeenCalledWith({
      kind: "topic",
      id: "t-bare",
      chunkId: undefined,
      documentId: undefined,
    });
  });

  it("toggle invokes onToggleValidated with the new value", () => {
    const onToggle = vi.fn();
    render(
      renderResults({
        snapshot: snapshot({ state: "data", response: POPULATED }),
        validatedOnly: true,
        onToggleValidated: onToggle,
      }),
    );
    fireEvent.click(screen.getByTestId("kx-search-validated-toggle"));
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
