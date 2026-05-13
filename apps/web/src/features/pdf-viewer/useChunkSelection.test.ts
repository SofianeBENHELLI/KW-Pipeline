/**
 * Unit coverage for the chunk-selection state machine.
 *
 * The hook itself lives in
 * ``apps/_shared/pdf-viewer/useChunkSelection.ts`` so every frontend
 * gets the same state machine; this spec exercises it via the
 * relative import to keep Orbital's vitest config the single test
 * runner for the shared module (no per-app duplication needed).
 */

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useChunkSelection } from "../../../../_shared/pdf-viewer";

describe("useChunkSelection (shared)", () => {
  it("starts with no selection or hover", () => {
    const { result } = renderHook(() => useChunkSelection());
    expect(result.current.selectedChunkId).toBeNull();
    expect(result.current.hoveredChunkId).toBeNull();
  });

  it("promotes a chunk via selectChunk and persists across re-renders", () => {
    const { result, rerender } = renderHook(() => useChunkSelection());
    act(() => result.current.selectChunk("page-1-sec-0"));
    expect(result.current.selectedChunkId).toBe("page-1-sec-0");
    rerender();
    expect(result.current.selectedChunkId).toBe("page-1-sec-0");
  });

  it("tracks hover state independently of selection", () => {
    const { result } = renderHook(() => useChunkSelection());
    act(() => result.current.selectChunk("page-1-sec-0"));
    act(() => result.current.hoverChunk("page-2-sec-3"));
    expect(result.current.selectedChunkId).toBe("page-1-sec-0");
    expect(result.current.hoveredChunkId).toBe("page-2-sec-3");
  });

  it("clear() resets both selection and hover", () => {
    const { result } = renderHook(() => useChunkSelection());
    act(() => result.current.selectChunk("page-1-sec-0"));
    act(() => result.current.hoverChunk("page-2-sec-1"));
    act(() => result.current.clear());
    expect(result.current.selectedChunkId).toBeNull();
    expect(result.current.hoveredChunkId).toBeNull();
  });

  it("accepts null to deselect without clearing hover", () => {
    const { result } = renderHook(() => useChunkSelection());
    act(() => result.current.selectChunk("page-1-sec-0"));
    act(() => result.current.hoverChunk("page-2-sec-1"));
    act(() => result.current.selectChunk(null));
    expect(result.current.selectedChunkId).toBeNull();
    expect(result.current.hoveredChunkId).toBe("page-2-sec-1");
  });
});
