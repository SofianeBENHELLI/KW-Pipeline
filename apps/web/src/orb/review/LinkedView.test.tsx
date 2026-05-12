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

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LinkedView } from "./LinkedView";
import { projectGraph } from "../hooks/useLinkedObjects";
import type { ApiKnowledgeGraphProjection } from "../../api/types";

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
});
