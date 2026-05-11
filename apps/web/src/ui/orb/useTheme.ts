import { useCallback, useEffect, useState } from "react";

export type OrbTheme = "light" | "dark";
export type OrbDensity = "cozy" | "normal" | "dense";

const THEME_STORAGE_KEY = "orb:theme";
const DENSITY_STORAGE_KEY = "orb:density";

function readStorage<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  if (typeof window === "undefined") return fallback;
  const raw = window.localStorage.getItem(key);
  return (allowed as readonly string[]).includes(raw ?? "") ? (raw as T) : fallback;
}

function detectInitialTheme(): OrbTheme {
  const stored = readStorage<OrbTheme>(THEME_STORAGE_KEY, ["light", "dark"] as const, "light");
  if (typeof window !== "undefined" && window.localStorage.getItem(THEME_STORAGE_KEY) !== null) {
    return stored;
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return stored;
}

export function useOrbTheme(): {
  theme: OrbTheme;
  setTheme: (next: OrbTheme) => void;
  toggleTheme: () => void;
} {
  const [theme, setThemeState] = useState<OrbTheme>(() => detectInitialTheme());

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.dataset.theme = theme;
    }
  }, [theme]);

  const setTheme = useCallback((next: OrbTheme) => {
    setThemeState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((prev) => {
      const next = prev === "light" ? "dark" : "light";
      if (typeof window !== "undefined") {
        window.localStorage.setItem(THEME_STORAGE_KEY, next);
      }
      return next;
    });
  }, []);

  return { theme, setTheme, toggleTheme };
}

export function useOrbDensity(): {
  density: OrbDensity;
  setDensity: (next: OrbDensity) => void;
} {
  const [density, setDensityState] = useState<OrbDensity>(() =>
    readStorage<OrbDensity>(DENSITY_STORAGE_KEY, ["cozy", "normal", "dense"] as const, "normal"),
  );

  useEffect(() => {
    if (typeof document !== "undefined") {
      if (density === "normal") {
        delete document.documentElement.dataset.density;
      } else {
        document.documentElement.dataset.density = density;
      }
    }
  }, [density]);

  const setDensity = useCallback((next: OrbDensity) => {
    setDensityState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(DENSITY_STORAGE_KEY, next);
    }
  }, []);

  return { density, setDensity };
}
