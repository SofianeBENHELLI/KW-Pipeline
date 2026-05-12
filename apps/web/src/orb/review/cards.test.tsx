/**
 * Tests for the simpler Review-tab cards: DocumentDetailCard,
 * VersionList, RawExtractionTabs, SemanticMarkdownCard,
 * BatchBanner, PipelineTab.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ApiDocument, ApiRawExtraction, ApiSemanticDocument } from "../../api/types";
import { BatchBanner } from "./BatchBanner";
import { DocumentDetailCard } from "./DocumentDetailCard";
import { PipelineTab } from "./PipelineTab";
import { RawExtractionTabs } from "./RawExtractionTabs";
import { SemanticMarkdownCard } from "./SemanticMarkdownCard";
import { VersionList } from "./VersionList";

function fixtureDoc(): ApiDocument {
  return {
    id: "doc-1",
    original_filename: "policy.txt",
    latest_version_id: "ver-2",
    created_at: "2026-04-30T08:42:00Z",
    archived_at: null,
    scopes: [],
    versions: [
      {
        id: "ver-1",
        document_id: "doc-1",
        version_number: 1,
        filename: "policy.txt",
        content_type: "text/plain",
        file_size: 1024,
        sha256: "h1",
        storage_uri: "file://1",
        status: "EXTRACTED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: null,
        reviewed_at: null,
        created_at: "2026-04-29T08:42:00Z",
      },
      {
        id: "ver-2",
        document_id: "doc-1",
        version_number: 2,
        filename: "policy.txt",
        content_type: "text/plain",
        file_size: 2048,
        sha256: "h2deadbeef",
        storage_uri: "file://2",
        status: "VALIDATED",
        duplicate_of_version_id: null,
        failure_reason: null,
        reviewer_note: "looks good",
        reviewed_at: "2026-04-30T09:00:00Z",
        created_at: "2026-04-30T08:42:00Z",
      },
    ],
  };
}

describe("<DocumentDetailCard />", () => {
  it("renders the meta rows for a real document", () => {
    render(<DocumentDetailCard document={fixtureDoc()} />);
    expect(screen.getByText("ID")).toBeInTheDocument();
    expect(screen.getByText("doc-1")).toBeInTheDocument();
    expect(screen.getByText("policy.txt")).toBeInTheDocument();
    expect(screen.getByText(/h2deadbeef/)).toBeInTheDocument();
  });

  it("renders the empty state when no document is selected", () => {
    render(<DocumentDetailCard document={null} />);
    expect(screen.getByText(/Pick a document from the rail/i)).toBeInTheDocument();
  });
});

describe("<VersionList />", () => {
  it("renders the latest two versions sorted desc and flags the current row", () => {
    const { container } = render(<VersionList document={fixtureDoc()} />);
    const rows = container.querySelectorAll(".kf-versions__row");
    expect(rows.length).toBe(2);
    expect(rows[0]).toHaveClass("is-cur");
    expect(rows[0]).toHaveTextContent("v2");
  });

  it("renders the empty message for a doc with no versions", () => {
    const empty = { ...fixtureDoc(), versions: [] } as ApiDocument;
    render(<VersionList document={empty} />);
    expect(screen.getByText("No versions yet.")).toBeInTheDocument();
  });
});

const FAKE_EXTR: ApiRawExtraction = {
  document_id: "doc-1",
  version_id: "ver-2",
  schema_version: "v0.1",
  parser_name: "poppler",
  parser_version: "v24",
  warnings: [],
  sections: [
    { heading: "Intro", spans: [], page_start: 1, page_end: 1 },
    { heading: "Tables: ARR walk", spans: [], page_start: 2, page_end: 2 },
  ],
  source_references: [],
} as unknown as ApiRawExtraction;

describe("<RawExtractionTabs />", () => {
  it("renders the json tab by default", () => {
    render(<RawExtractionTabs status="ok" extraction={FAKE_EXTR} />);
    expect(screen.getByTestId("kf-extr-json")).toBeInTheDocument();
  });

  it("switches to the spans tab", () => {
    render(<RawExtractionTabs status="ok" extraction={FAKE_EXTR} />);
    fireEvent.click(screen.getByRole("tab", { name: "page-spans" }));
    expect(screen.getByTestId("kf-extr-spans")).toBeInTheDocument();
  });

  it("renders the absent placeholder on 'absent'", () => {
    render(<RawExtractionTabs status="absent" extraction={null} />);
    expect(
      screen.getByText(/No extraction yet/i),
    ).toBeInTheDocument();
  });

  it("renders the error placeholder", () => {
    render(<RawExtractionTabs status="error" extraction={null} errorMessage="500" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load extraction/);
  });
});

const FAKE_SEM: ApiSemanticDocument = {
  id: "sem-1",
  document_version_id: "ver-2",
  schema_version: "v0.1",
  document_profile: {
    title: "Policy",
    document_type: "unknown",
    purpose: null,
    audience: null,
    executive_summary: null,
  },
  sections: [],
  assets: [],
  warnings: [],
  source_references: [],
  validation_status: "validated",
  markdown: "# Sample\n\ntext",
  created_at: "2026-04-30T09:00:00Z",
} as unknown as ApiSemanticDocument;

describe("<SemanticMarkdownCard />", () => {
  it("renders the preview tab by default", () => {
    render(
      <SemanticMarkdownCard
        status="ok"
        semantic={FAKE_SEM}
        markdown="# Hello"
      />,
    );
    expect(screen.getByTestId("kf-sem-preview")).toBeInTheDocument();
    expect(screen.getByText(/# Hello/)).toBeInTheDocument();
  });

  it("switches to source", () => {
    render(<SemanticMarkdownCard status="ok" semantic={FAKE_SEM} markdown={null} />);
    fireEvent.click(screen.getByRole("tab", { name: /source/ }));
    expect(screen.getByTestId("kf-sem-source")).toBeInTheDocument();
  });

  it("labels the diff tab when previousVersion is given", () => {
    render(
      <SemanticMarkdownCard
        status="ok"
        semantic={FAKE_SEM}
        markdown={null}
        previousVersion={3}
      />,
    );
    expect(screen.getByRole("tab", { name: "diff vs v3" })).toBeInTheDocument();
  });

  it("renders absent placeholder on 'absent'", () => {
    render(<SemanticMarkdownCard status="absent" semantic={null} markdown={null} />);
    expect(screen.getByText(/No semantic output yet/i)).toBeInTheDocument();
  });
});

describe("<BatchBanner />", () => {
  it("returns null when snapshot is null", () => {
    const { container } = render(
      <BatchBanner snapshot={null} onDismiss={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders done/failed/in-flight counts and failures", () => {
    const onDismiss = vi.fn();
    render(
      <BatchBanner
        snapshot={{
          progress: new Map([
            ["d1", "done"],
            ["d2", "failed"],
            ["d3", "extracting"],
          ]),
          failures: [{ docId: "d2", reason: "semantic_extract: empty" }],
          total: 3,
        }}
        onDismiss={onDismiss}
      />,
    );
    expect(
      screen.getByText(/1 done · 1 failed · 1 in-flight/),
    ).toBeInTheDocument();
    expect(screen.getByText(/d2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("<PipelineTab />", () => {
  it("renders one row per version with the latest flagged", () => {
    render(<PipelineTab document={fixtureDoc()} />);
    const list = screen.getByTestId("kf-pipeline-list");
    expect(list.querySelectorAll(".kf-pipeline__row").length).toBe(2);
    expect(screen.getByText("latest")).toBeInTheDocument();
  });

  it("renders the empty state when no document is selected", () => {
    render(<PipelineTab document={null} />);
    expect(screen.getByText(/Pick a document from the rail/i)).toBeInTheDocument();
  });
});
