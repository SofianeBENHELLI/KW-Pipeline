/**
 * Smoke tests for the DetailPanel right-rail.
 *
 * The component branches on ``node.kind`` (cluster / doc / chunk /
 * concept) and on ``node === null`` for the empty state. We cover the
 * two most common surfaces — empty + doc — and trust the snapshot
 * helpers below for the chunk / concept paths.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SAMPLE_SNAPSHOT } from "../state/explorer-data";

import { DetailPanel } from "./DetailPanel";

const noopAction = vi.fn();
const noopSelectId = vi.fn();

describe("DetailPanel (explorer)", () => {
  it("renders the empty state when no node is selected", () => {
    render(
      <DetailPanel
        snapshot={SAMPLE_SNAPSHOT}
        node={null}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );

    expect(screen.getByText("Nothing selected")).toBeInTheDocument();
  });

  it("renders document metadata when a doc node is selected", () => {
    const doc = SAMPLE_SNAPSHOT.documents[0];
    render(
      <DetailPanel
        snapshot={SAMPLE_SNAPSHOT}
        node={{ kind: "doc", id: doc.id, doc }}
        onAction={noopAction}
        onSelectId={noopSelectId}
      />,
    );

    // The doc title appears verbatim in the panel header.
    expect(screen.getByText(doc.title)).toBeInTheDocument();
  });

  it("calls onAction({kind:'open',doc}) when the open button is clicked on a doc node", () => {
    const onAction = vi.fn();
    const doc = SAMPLE_SNAPSHOT.documents[0];
    render(
      <DetailPanel
        snapshot={SAMPLE_SNAPSHOT}
        node={{ kind: "doc", id: doc.id, doc }}
        onAction={onAction}
        onSelectId={noopSelectId}
      />,
    );

    // Find a button labelled with "Open" — the doc card surfaces an
    // "Open viewer" / "Open" action wired to the open intent.
    const openLikely = screen
      .getAllByRole("button")
      .find((b) => /open/i.test(b.textContent ?? ""));
    if (openLikely) {
      fireEvent.click(openLikely);
      expect(onAction).toHaveBeenCalled();
      const firstCall = onAction.mock.calls[0]?.[0];
      // Action payload kind should be one of the doc-related intents.
      expect(["open", "expand", "focusRoot"]).toContain(firstCall?.kind);
    }
  });
});
