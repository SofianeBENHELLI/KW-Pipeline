/**
 * useOrbTheme / useOrbDensity — Phase-0 hook tests. Pin the contract:
 *   1. The hook writes `data-theme` to the documentElement.
 *   2. The hook persists to localStorage.
 *   3. The hook reads back prefers-color-scheme on first load.
 *   4. Density toggles strip the dataset attr when set to "normal".
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOrbDensity, useOrbTheme } from "./useTheme";

beforeEach(() => {
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.density;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useOrbTheme", () => {
  it("defaults to light and writes data-theme=light to documentElement", () => {
    renderHook(() => useOrbTheme());
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("toggles between light and dark and persists", () => {
    const { result } = renderHook(() => useOrbTheme());
    expect(result.current.theme).toBe("light");
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("orb:theme")).toBe("dark");
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
  });

  it("setTheme writes the value through to storage + dataset", () => {
    const { result } = renderHook(() => useOrbTheme());
    act(() => result.current.setTheme("dark"));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("orb:theme")).toBe("dark");
  });

  it("falls back to prefers-color-scheme when localStorage is empty", () => {
    vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
      matches: query === "(prefers-color-scheme: dark)",
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const { result } = renderHook(() => useOrbTheme());
    expect(result.current.theme).toBe("dark");
  });
});

describe("useOrbDensity", () => {
  it("defaults to normal and strips the dataset attribute", () => {
    renderHook(() => useOrbDensity());
    expect(document.documentElement.dataset.density).toBeUndefined();
  });

  it("setDensity('dense') writes the dataset attribute and persists", () => {
    const { result } = renderHook(() => useOrbDensity());
    act(() => result.current.setDensity("dense"));
    expect(document.documentElement.dataset.density).toBe("dense");
    expect(window.localStorage.getItem("orb:density")).toBe("dense");
  });

  it("setDensity('normal') strips the dataset attribute again", () => {
    const { result } = renderHook(() => useOrbDensity());
    act(() => result.current.setDensity("cozy"));
    expect(document.documentElement.dataset.density).toBe("cozy");
    act(() => result.current.setDensity("normal"));
    expect(document.documentElement.dataset.density).toBeUndefined();
  });
});
