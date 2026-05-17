/**
 * LinkedView — pin the cross-highlight (the flagship interaction):
 *
 *  1. Hover a Topic card → its source chunks light up in the doc.
 *  2. Hover an Entity card → its source chunks light up.
 *  3. Hover a chunk span in the doc → the parent Topic card lights up.
 *  4. Hover a chunk span → its Entity cards light up too.
 *
 * Also covers the Topics/Entities/Chunks segmented control + the
 * loading / empty / error states.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LinkedView } from "./LinkedView";
import { projectGraph } from "../hooks/useLinkedObjects";
import type { ApiKnowledgeGraphProjection } from "../../api/types";

// The real ``PdfViewerPanel`` fetches ``/raw`` bytes and runs the
// pdfjs-dist render loop — both useless in jsdom. The PDF-mode cases
// below replace it with a stub that records every render's props so
// we can assert the cross-highlight wiring without standing up a
// real PDF pipeline. The mock is hoisted to module scope by
// ``vi.mock`` so it is in effect for the whole file.
const _pdfViewerPanelMock = vi.fn();
vi.mock("../../features/pdf-viewer", () => ({
  PdfViewerPanel: (props: Record<string, unknown>) => {
    _pdfViewerPanelMock(props);
    return <div data-testid="kf-pdf-viewer-stub" />;
  },
}));

function _lastPdfPanelProps(): Record<string, unknown> {
  expect(_pdfViewerPanelMock).toHaveBeenCalled();
  const calls = _pdfViewerPanelMock.mock.calls;
  return calls[calls.length - 1][0];
}

function _fireHoverChunkFromPdfViewer(chunkId: string | null): void {
  const props = _lastPdfPanelProps();
  const onHover = props.onHoverChunk as (id: string | null) => void;
  act(() => onHover(chunkId));
}

const PROJECTION: ApiKnowledgeGraphProjection = {
  document_id: "doc-1",
  version_id: "ver-1",
  generated_at: "2026-05-12T09:00:00Z",
  schema_version: "v0.2",
  nodes: [
    {
      id: "c1",
      kind: "chunk",
      label: "chunk-1",
      properties: { text: "Net new ARR closed at $8.4M.", page: 1 },
    },
    {
      id: "c2",
      kind: "chunk",
      label: "chunk-2",
      properties: { text: "Expansion dragged plan by $0.4M.", page: 1 },
    },
    {
      id: "c3",
      kind: "chunk",
      label: "chunk-3",
      properties: { text: "Renewal cohort slipped four contracts.", page: 2 },
    },
    {
      id: "t1",
      kind: "topic",
      label: "arr-walk",
      properties: { keywords: ["netnew", "churn", "plan"] },
    },
    {
      id: "t2",
      kind: "topic",
      label: "renewal-slip",
      properties: { keywords: ["renewal", "SLA"] },
    },
    {
      id: "e1",
      kind: "entity",
      label: "$8.4M NetNew",
      properties: { type: "monetary" },
    },
  ],
  edges: [
    { id: "e_b1", kind: "belongs_to", source_id: "c1", target_id: "t1", properties: {} },
    { id: "e_b2", kind: "belongs_to", source_id: "c2", target_id: "t1", properties: {} },
    { id: "e_b3", kind: "belongs_to", source_id: "c3", target_id: "t2", properties: {} },
    { id: "e_h1", kind: "has_entity", source_id: "c1", target_id: "e1", properties: {} },
  ],
};

const FIXTURE = projectGraph(PROJECTION);

describe("<LinkedView />", () => {
  it("renders the document body with one span per chunk", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    expect(screen.getByTestId("kf-lv-span-c1")).toBeInTheDocument();
    expect(screen.getByTestId("kf-lv-span-c2")).toBeInTheDocument();
    expect(screen.getByTestId("kf-lv-span-c3")).toBeInTheDocument();
  });

  it("defaults to the Topics tab and renders a card per topic", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    expect(screen.getByRole("tab", { name: /Topics/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByTestId("kf-lv-obj-Topics-t1")).toBeInTheDocument();
    expect(screen.getByTestId("kf-lv-obj-Topics-t2")).toBeInTheDocument();
  });

  it("hovering a Topic card highlights its source chunks in the doc", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    fireEvent.mouseEnter(screen.getByTestId("kf-lv-obj-Topics-t1"));
    expect(screen.getByTestId("kf-lv-span-c1")).toHaveClass("is-hl");
    expect(screen.getByTestId("kf-lv-span-c2")).toHaveClass("is-hl");
    expect(screen.getByTestId("kf-lv-span-c3")).not.toHaveClass("is-hl");
  });

  it("hovering an Entity card highlights its source chunks", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    fireEvent.click(screen.getByRole("tab", { name: /Entities/ }));
    fireEvent.mouseEnter(screen.getByTestId("kf-lv-obj-Entities-e1"));
    expect(screen.getByTestId("kf-lv-span-c1")).toHaveClass("is-hl");
    expect(screen.getByTestId("kf-lv-span-c2")).not.toHaveClass("is-hl");
  });

  it("hovering a chunk in the doc highlights its parent Topic card", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    fireEvent.mouseEnter(screen.getByTestId("kf-lv-span-c3"));
    expect(screen.getByTestId("kf-lv-obj-Topics-t2")).toHaveClass("is-hl");
    expect(screen.getByTestId("kf-lv-obj-Topics-t1")).not.toHaveClass("is-hl");
  });

  it("hovering a chunk highlights its Entity cards (after switching tab)", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    fireEvent.click(screen.getByRole("tab", { name: /Entities/ }));
    fireEvent.mouseEnter(screen.getByTestId("kf-lv-span-c1"));
    expect(screen.getByTestId("kf-lv-obj-Entities-e1")).toHaveClass("is-hl");
  });

  it("clears the highlight on mouseLeave", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    const t1 = screen.getByTestId("kf-lv-obj-Topics-t1");
    fireEvent.mouseEnter(t1);
    expect(screen.getByTestId("kf-lv-span-c1")).toHaveClass("is-hl");
    fireEvent.mouseLeave(t1);
    expect(screen.getByTestId("kf-lv-span-c1")).not.toHaveClass("is-hl");
  });

  it("switching to the Chunks tab renders one card per chunk with topic label", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    fireEvent.click(screen.getByRole("tab", { name: /Chunks/ }));
    const card = screen.getByTestId("kf-lv-obj-Chunks-c1");
    expect(within(card).getByText(/arr-walk/)).toBeInTheDocument();
  });

  it("renders the loading state", () => {
    render(<LinkedView documentId="doc-1" loading />);
    expect(screen.getByTestId("kf-linked-loading")).toBeInTheDocument();
  });

  it("renders the empty state when there are no chunks", () => {
    const empty = projectGraph({
      document_id: "x",
      version_id: "v",
      generated_at: "x",
      schema_version: "v0.2",
      nodes: [],
      edges: [],
    });
    render(<LinkedView documentId="doc-1" fixture={empty} />);
    expect(screen.getByTestId("kf-linked-empty")).toBeInTheDocument();
  });

  it("renders the foot status text + flips when hovering", () => {
    render(<LinkedView documentId="doc-1" filename="x.md" fixture={FIXTURE} />);
    expect(
      screen.getByText(/hover an object to highlight its source span/i),
    ).toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByTestId("kf-lv-obj-Topics-t1"));
    expect(screen.getByText(/cross-highlighting/i)).toBeInTheDocument();
  });

  describe("PDF mode", () => {
    it("renders the PDF viewer panel instead of the text article when `pdf` is set", () => {
      render(
        <LinkedView
          documentId="doc-1"
          filename="policy.pdf"
          pdf={{ versionId: "v-1", expectedHash: "abc123def456" }}
          fixture={FIXTURE}
        />,
      );
      // Left pane switches from the text-card layout (data-testid
      // ``kf-lv-text``) to the PDF embed (data-testid ``kf-lv-pdf``).
      expect(screen.getByTestId("kf-lv-pdf")).toBeInTheDocument();
      expect(screen.queryByTestId("kf-lv-text")).not.toBeInTheDocument();
      // The text-article section headings (rendered as ``kf-lv-section-*``
      // testids) do not appear in PDF mode.
      expect(screen.queryByTestId("kf-lv-section-s1")).not.toBeInTheDocument();
      // The right pane (Topics / Entities / Chunks) keeps rendering so
      // operators do not lose knowledge-object navigation.
      expect(screen.getByTestId("kf-lv-obj-Topics-t1")).toBeInTheDocument();
    });

    it("renders the PDF pane even when the linked-objects projection is empty", () => {
      // A freshly-uploaded PDF that has not been semantically projected
      // yet should still render the actual bytes in the viewer — the
      // ``kf-linked-empty`` short-circuit only fires for non-PDF docs.
      const empty = projectGraph({
        document_id: "x",
        version_id: "v",
        generated_at: "x",
        schema_version: "v0.2",
        nodes: [],
        edges: [],
      });
      render(
        <LinkedView
          documentId="doc-1"
          filename="fresh.pdf"
          pdf={{ versionId: "v-1", expectedHash: "abc123def456" }}
          fixture={empty}
        />,
      );
      expect(screen.queryByTestId("kf-linked-empty")).not.toBeInTheDocument();
      expect(screen.getByTestId("kf-lv-pdf")).toBeInTheDocument();
    });

    it("stays on the text article when `pdf` is null (non-PDF documents)", () => {
      render(
        <LinkedView
          documentId="doc-1"
          filename="x.md"
          pdf={null}
          fixture={FIXTURE}
        />,
      );
      expect(screen.getByTestId("kf-lv-text")).toBeInTheDocument();
      expect(screen.queryByTestId("kf-lv-pdf")).not.toBeInTheDocument();
    });

    describe("cross-highlight wiring", () => {
      it("feeds an empty hover set to the PDF panel by default", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        const props = _lastPdfPanelProps();
        const ids = props.externalHoveredChunkIds as ReadonlySet<string>;
        expect(ids.size).toBe(0);
        expect(props.hideBuiltInSidePanel).toBe(true);
      });

      it("hovering a Topic lights up every chunk that belongs to it", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        // Topic ``t1`` owns chunks c1 + c2 in the fixture.
        fireEvent.mouseEnter(screen.getByTestId("kf-lv-obj-Topics-t1"));
        const props = _lastPdfPanelProps();
        const ids = props.externalHoveredChunkIds as ReadonlySet<string>;
        expect(ids.has("c1")).toBe(true);
        expect(ids.has("c2")).toBe(true);
        expect(ids.has("c3")).toBe(false);
      });

      it("hovering an Entity lights up every chunk that cites it", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        // Entity ``e1`` is cited by c1 only in the fixture. Switch the
        // right-pane segmented control to Entities so the card is in
        // the DOM, then hover it.
        fireEvent.click(screen.getByRole("tab", { name: /Entities/ }));
        fireEvent.mouseEnter(screen.getByTestId("kf-lv-obj-Entities-e1"));
        const ids = _lastPdfPanelProps().externalHoveredChunkIds as ReadonlySet<string>;
        expect(ids.has("c1")).toBe(true);
        expect(ids.has("c2")).toBe(false);
      });

      it("PDF rect hover (via onHoverChunk callback) lights up the parent Topic card", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        // Simulate the shared viewer firing its hover callback with
        // chunk c1 — should light up its parent topic (t1) on the
        // right pane via the existing ``is-hl`` class.
        _fireHoverChunkFromPdfViewer("c1");
        expect(screen.getByTestId("kf-lv-obj-Topics-t1").className).toMatch(/is-hl/);
        // Clearing the hover removes the highlight.
        _fireHoverChunkFromPdfViewer(null);
        expect(screen.getByTestId("kf-lv-obj-Topics-t1").className).not.toMatch(/is-hl/);
      });

      it("threads initialChunkId into the PDF panel as externalSelectedChunkIds (chat deep-link)", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
            initialChunkId="c2"
          />,
        );
        const props = _lastPdfPanelProps();
        const selected = props.externalSelectedChunkIds as ReadonlySet<string>;
        expect(selected.has("c2")).toBe(true);
        expect(selected.size).toBe(1);
      });

      it("omits the deep-link selection when no initialChunkId is set", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        expect(_lastPdfPanelProps().externalSelectedChunkIds).toBeNull();
      });

      it("hovering a Chunk card lights up just that chunk in the PDF", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        // Switch to the Chunks tab so the Chunk cards are in the DOM.
        fireEvent.click(screen.getByRole("tab", { name: /Chunks/ }));
        fireEvent.mouseEnter(screen.getByTestId("kf-lv-obj-Chunks-c3"));
        const ids = _lastPdfPanelProps().externalHoveredChunkIds as ReadonlySet<string>;
        expect([...ids]).toEqual(["c3"]);
      });
    });

    describe("coverage toggle", () => {
      beforeEach(() => window.localStorage.clear());

      it("forwards coverageMode=false by default", () => {
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        expect(_lastPdfPanelProps().coverageMode).toBe(false);
      });

      it("toggling the checkbox flips coverageMode and persists", () => {
        _pdfViewerPanelMock.mockClear();
        const { unmount } = render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        const toggle = screen.getByTestId("kf-lv-coverage-toggle");
        fireEvent.click(within(toggle).getByRole("checkbox"));
        expect(_lastPdfPanelProps().coverageMode).toBe(true);
        expect(window.localStorage.getItem("kf:review:coverage-mode")).toBe(
          "true",
        );

        // Remount → hydrates from localStorage.
        unmount();
        _pdfViewerPanelMock.mockClear();
        render(
          <LinkedView
            documentId="doc-1"
            filename="policy.pdf"
            pdf={{ versionId: "v-1", expectedHash: "abc" }}
            fixture={FIXTURE}
          />,
        );
        expect(_lastPdfPanelProps().coverageMode).toBe(true);
      });

      it("does not render the toggle for non-PDF documents", () => {
        render(
          <LinkedView
            documentId="doc-1"
            filename="notes.md"
            pdf={null}
            fixture={FIXTURE}
          />,
        );
        expect(
          screen.queryByTestId("kf-lv-coverage-toggle"),
        ).not.toBeInTheDocument();
      });
    });
  });
});
