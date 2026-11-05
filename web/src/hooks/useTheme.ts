import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "clashflow-theme";

/** The inline script in index.html already applied the stored theme; this hook
 * adopts it, then keeps <html data-theme> and localStorage in sync. */
export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const applied = document.documentElement.dataset.theme;
    if (applied === "dark" || applied === "light") return applied;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Private mode: the switch still works for this page load.
      }
      return next;
    });
  }, []);

  return { theme, toggle };
}
