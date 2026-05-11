import { useCallback, useEffect, useState } from "react";

export type OrbTheme = "light" | "dark";
const KEY = "orb:theme";

function detect(): OrbTheme {
  if (typeof window === "undefined") return "light";
  const stored = window.localStorage.getItem(KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useOrbTheme() {
  const [theme, setThemeState] = useState<OrbTheme>(() => detect());

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.dataset.theme = theme;
    }
  }, [theme]);

  const setTheme = useCallback((next: OrbTheme) => {
    setThemeState(next);
    if (typeof window !== "undefined") window.localStorage.setItem(KEY, next);
  }, []);

  const toggle = useCallback(() => {
    setThemeState((prev) => {
      const next = prev === "light" ? "dark" : "light";
      if (typeof window !== "undefined") window.localStorage.setItem(KEY, next);
      return next;
    });
  }, []);

  return { theme, setTheme, toggle };
}
