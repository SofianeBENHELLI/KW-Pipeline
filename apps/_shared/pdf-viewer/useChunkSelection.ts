/**
 * Selection state shared between the PDF overlay and the side panel.
 *
 * Both surfaces subscribe to ``selectedChunkId`` and ``hoveredChunkId``.
 * Clicks in either pane converge on ``selectChunk`` so the
 * bidirectional sync flows through a single source of truth — no
 * cross-effect spaghetti between the two components.
 *
 * The hook is intentionally tiny: just a small state machine on top of
 * ``useState`` with stable action references so consumers can pass them
 * to ``useCallback`` deps without re-rendering on every parent tick.
 */

import { useCallback, useMemo, useState } from "react";

export interface ChunkSelectionState {
  /** Sticky selection — set by clicks. Null until the first interaction. */
  readonly selectedChunkId: string | null;
  /** Transient hover state — set by mouseenter / cleared by mouseleave. */
  readonly hoveredChunkId: string | null;
}

export interface ChunkSelectionActions {
  readonly selectChunk: (chunkId: string | null) => void;
  readonly hoverChunk: (chunkId: string | null) => void;
  readonly clear: () => void;
}

export type ChunkSelection = ChunkSelectionState & ChunkSelectionActions;

export function useChunkSelection(): ChunkSelection {
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [hoveredChunkId, setHoveredChunkId] = useState<string | null>(null);

  const selectChunk = useCallback((chunkId: string | null) => {
    setSelectedChunkId(chunkId);
  }, []);

  const hoverChunk = useCallback((chunkId: string | null) => {
    setHoveredChunkId(chunkId);
  }, []);

  const clear = useCallback(() => {
    setSelectedChunkId(null);
    setHoveredChunkId(null);
  }, []);

  return useMemo(
    () => ({
      selectedChunkId,
      hoveredChunkId,
      selectChunk,
      hoverChunk,
      clear,
    }),
    [selectedChunkId, hoveredChunkId, selectChunk, hoverChunk, clear],
  );
}
