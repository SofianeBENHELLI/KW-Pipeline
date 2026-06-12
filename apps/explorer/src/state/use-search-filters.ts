/**
 * Persistent state for the Explorer search panel filters
 * (#320 partial — score threshold + persistent toggle).
 *
 * The two filter knobs (validated/source-backed only, minimum score)
 * survive page reloads via the widget's key-value store, mirroring
 * how ``apiBaseUrl`` is persisted by ``api/client.ts``. Storing them
 * on the widget side rather than ``localStorage`` keeps the explorer
 * working when embedded in 3DEXPERIENCE iframes that sandbox
 * cross-origin storage.
 *
 * The full #320 EPIC also wants URL-hash-driven deep links so a user
 * can paste a filtered search to a colleague. That stays deferred —
 * the URL contract bleeds into the App router and the issue's
 * "atlas/search/lens consistency" requirement, which needs lens
 * surfaces to exist first. This hook is the surgical slice that
 * delivers the everyday persistence win without touching routing.
 */

import { useCallback, useEffect, useState } from "react";

import { widget } from "@widget-lab/3ddashboard-utils";

const VALIDATED_KEY = "kx-search-validated-only";
const SCORE_THRESHOLD_KEY = "kx-search-score-threshold";
const HIDE_DEMO_KEY = "kx-hide-demo-docs";

const DEFAULT_VALIDATED_ONLY = true;
const DEFAULT_SCORE_THRESHOLD = 0;
const MIN_SCORE_THRESHOLD = 0;
const MAX_SCORE_THRESHOLD = 1;

function safeGet(key: string): string | null {
  try {
    const v = widget.getValue(key);
    return typeof v === "string" && v.length > 0 ? v : null;
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    widget.setValue(key, value);
  } catch {
    // Best-effort; persistence is unavailable when running outside the host.
  }
}

function readValidatedOnly(): boolean {
  const raw = safeGet(VALIDATED_KEY);
  if (raw === null) return DEFAULT_VALIDATED_ONLY;
  return raw === "true";
}

function readScoreThreshold(): number {
  const raw = safeGet(SCORE_THRESHOLD_KEY);
  if (raw === null) return DEFAULT_SCORE_THRESHOLD;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return DEFAULT_SCORE_THRESHOLD;
  return clampThreshold(parsed);
}

function readHideDemo(): boolean | null {
  const raw = safeGet(HIDE_DEMO_KEY);
  if (raw === "true") return true;
  if (raw === "false") return false;
  // No stored preference → null. The App resolves null to its
  // "auto" rule: hide demo rows only when the corpus mixes demo and
  // operator documents, so a pure-demo environment stays visible
  // right after "Load demo" while a production corpus never mixes.
  return null;
}

export function clampThreshold(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_SCORE_THRESHOLD;
  if (value < MIN_SCORE_THRESHOLD) return MIN_SCORE_THRESHOLD;
  if (value > MAX_SCORE_THRESHOLD) return MAX_SCORE_THRESHOLD;
  return value;
}

export interface SearchFilters {
  validatedOnly: boolean;
  scoreThreshold: number;
  /**
   * Demo-data visibility (Explorer Sprint 1). ``true`` hides demo
   * rows, ``false`` shows them, ``null`` means the operator never
   * chose — the App applies the auto rule (hide only when demo and
   * operator docs coexist).
   */
  hideDemo: boolean | null;
  setValidatedOnly: (next: boolean) => void;
  setScoreThreshold: (next: number) => void;
  setHideDemo: (next: boolean) => void;
}

export function useSearchFilters(): SearchFilters {
  // Lazy initialiser so the widget read happens once on mount, not on
  // every re-render. Both reads are bounded — ``safeGet`` swallows the
  // host's "no widget" exception and returns ``null``.
  const [validatedOnly, setValidatedOnlyState] = useState<boolean>(() =>
    readValidatedOnly(),
  );
  const [scoreThreshold, setScoreThresholdState] = useState<number>(() =>
    readScoreThreshold(),
  );
  const [hideDemo, setHideDemoState] = useState<boolean | null>(() =>
    readHideDemo(),
  );

  const setValidatedOnly = useCallback((next: boolean) => {
    setValidatedOnlyState(next);
  }, []);
  const setScoreThreshold = useCallback((next: number) => {
    setScoreThresholdState(clampThreshold(next));
  }, []);
  const setHideDemo = useCallback((next: boolean) => {
    setHideDemoState(next);
  }, []);

  // Persist changes asynchronously so the host doesn't block React's
  // commit phase. ``safeSet`` swallows host errors.
  useEffect(() => {
    safeSet(VALIDATED_KEY, validatedOnly ? "true" : "false");
  }, [validatedOnly]);
  useEffect(() => {
    safeSet(SCORE_THRESHOLD_KEY, String(scoreThreshold));
  }, [scoreThreshold]);
  useEffect(() => {
    // Only persist explicit choices — null (auto) must keep reading
    // as "no stored preference" on the next mount.
    if (hideDemo !== null) safeSet(HIDE_DEMO_KEY, hideDemo ? "true" : "false");
  }, [hideDemo]);

  return {
    validatedOnly,
    scoreThreshold,
    hideDemo,
    setValidatedOnly,
    setScoreThreshold,
    setHideDemo,
  };
}
