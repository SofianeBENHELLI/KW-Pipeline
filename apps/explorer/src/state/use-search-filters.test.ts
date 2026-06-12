/**
 * Hook tests for ``useSearchFilters`` (#320 partial). Pins:
 *
 *   * Defaults when the widget store is empty (validatedOnly=true,
 *     scoreThreshold=0).
 *   * Initial values restored from a populated store.
 *   * Setter calls persist back to the store.
 *   * Threshold setter clamps out-of-range / non-finite inputs.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { widget } from "@widget-lab/3ddashboard-utils";

import { clampThreshold, useSearchFilters } from "./use-search-filters";

const VALIDATED_KEY = "kx-search-validated-only";
const SCORE_THRESHOLD_KEY = "kx-search-score-threshold";
const HIDE_DEMO_KEY = "kx-hide-demo-docs";

const store = new Map<string, string>();

beforeEach(() => {
  store.clear();
  vi.spyOn(widget, "getValue").mockImplementation(
    (key: string) => store.get(key) ?? "",
  );
  vi.spyOn(widget, "setValue").mockImplementation(
    (key: string, value: string) => {
      store.set(key, value);
    },
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSearchFilters", () => {
  it("returns sensible defaults when the widget store is empty", () => {
    const { result } = renderHook(() => useSearchFilters());
    expect(result.current.validatedOnly).toBe(true);
    expect(result.current.scoreThreshold).toBe(0);
  });

  it("restores stored values on mount", () => {
    store.set(VALIDATED_KEY, "false");
    store.set(SCORE_THRESHOLD_KEY, "0.45");
    const { result } = renderHook(() => useSearchFilters());
    expect(result.current.validatedOnly).toBe(false);
    expect(result.current.scoreThreshold).toBe(0.45);
  });

  it("ignores a malformed score in the store and falls back to the default", () => {
    store.set(SCORE_THRESHOLD_KEY, "not-a-number");
    const { result } = renderHook(() => useSearchFilters());
    expect(result.current.scoreThreshold).toBe(0);
  });

  it("setValidatedOnly persists the new value via widget.setValue", () => {
    const { result } = renderHook(() => useSearchFilters());
    act(() => result.current.setValidatedOnly(false));
    expect(result.current.validatedOnly).toBe(false);
    expect(store.get(VALIDATED_KEY)).toBe("false");
  });

  it("setScoreThreshold persists the new value", () => {
    const { result } = renderHook(() => useSearchFilters());
    act(() => result.current.setScoreThreshold(0.6));
    expect(result.current.scoreThreshold).toBe(0.6);
    expect(store.get(SCORE_THRESHOLD_KEY)).toBe("0.6");
  });

  it("setScoreThreshold clamps out-of-range inputs to [0, 1]", () => {
    const { result } = renderHook(() => useSearchFilters());
    act(() => result.current.setScoreThreshold(-0.5));
    expect(result.current.scoreThreshold).toBe(0);
    act(() => result.current.setScoreThreshold(1.7));
    expect(result.current.scoreThreshold).toBe(1);
  });

  it("survives a widget host that throws on getValue / setValue", () => {
    vi.spyOn(widget, "getValue").mockImplementation(() => {
      throw new Error("no host");
    });
    vi.spyOn(widget, "setValue").mockImplementation(() => {
      throw new Error("no host");
    });
    const { result } = renderHook(() => useSearchFilters());
    // Defaults still apply, no throw.
    expect(result.current.validatedOnly).toBe(true);
    expect(result.current.scoreThreshold).toBe(0);
    // Setting also doesn't throw — the persistence is best-effort.
    act(() => result.current.setScoreThreshold(0.3));
    expect(result.current.scoreThreshold).toBe(0.3);
  });
});

describe("useSearchFilters — hideDemo (Sprint 1)", () => {
  it("defaults to null (auto) when the store has no preference", () => {
    const { result } = renderHook(() => useSearchFilters());
    expect(result.current.hideDemo).toBeNull();
  });

  it("restores an explicit stored choice on mount", () => {
    store.set(HIDE_DEMO_KEY, "false");
    const { result } = renderHook(() => useSearchFilters());
    expect(result.current.hideDemo).toBe(false);
  });

  it("setHideDemo persists the explicit choice", () => {
    const { result } = renderHook(() => useSearchFilters());
    act(() => result.current.setHideDemo(true));
    expect(result.current.hideDemo).toBe(true);
    expect(store.get(HIDE_DEMO_KEY)).toBe("true");
  });

  it("never persists the auto (null) state", () => {
    renderHook(() => useSearchFilters());
    expect(store.has(HIDE_DEMO_KEY)).toBe(false);
  });
});

describe("clampThreshold helper", () => {
  it("returns the input when within [0, 1]", () => {
    expect(clampThreshold(0)).toBe(0);
    expect(clampThreshold(0.5)).toBe(0.5);
    expect(clampThreshold(1)).toBe(1);
  });

  it("clamps out-of-range values to the bounds", () => {
    expect(clampThreshold(-1)).toBe(0);
    expect(clampThreshold(2)).toBe(1);
  });

  it("returns the default for non-finite values", () => {
    expect(clampThreshold(Number.NaN)).toBe(0);
    expect(clampThreshold(Number.POSITIVE_INFINITY)).toBe(0);
  });
});
