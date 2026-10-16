"use client";

import * as React from "react";

/**
 * Dark/light toggle for the dashboard.
 *
 * The brand kit is dark-only by rule -- it specs no light surface -- so this
 * is a self-designed addition, not a ported one. `app/globals.css` adjusts the
 * neutral ramp and status accents under `.light`; this provider tracks which class is on
 * `<html>` and persists the pick. No `next-themes` dependency: the app has
 * exactly two themes and no `dark:` utility variants to gate, so a class swap
 * plus localStorage is the whole feature.
 *
 * Default is dark (matches the product's look before this existed, and what
 * `INLINE_SET_THEME_SCRIPT` below paints before hydration). We do not follow
 * `prefers-color-scheme` -- an unset preference should not silently change how
 * the product has always looked for someone who never asked for light mode.
 */

export type Theme = "dark" | "light";

const STORAGE_KEY = "talaris-theme";

type ThemeContextValue = {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
};

const ThemeContext = React.createContext<ThemeContextValue | undefined>(undefined);

// Void Black / Off-White page grounds, kept in sync with <meta name="theme-color">
// so the browser chrome and overscroll area stay in the ramp instead of
// defaulting to white under the light theme.
const THEME_COLOR: Record<Theme, string> = {
  dark: "#0A0A0B",
  light: "#F7F6F4"
};

function applyThemeClass(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("light", theme === "light");
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
  root.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
}

// Runs as a blocking inline script in <head>, before React hydrates, so the
// stored preference paints on first frame instead of flashing dark-then-light.
export const INLINE_SET_THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${STORAGE_KEY}");if(t!=="light"&&t!=="dark")t="dark";var r=document.documentElement;r.classList.toggle("light",t==="light");r.classList.toggle("dark",t==="dark");r.style.colorScheme=t;var m=document.querySelector('meta[name="theme-color"]');if(m)m.setAttribute("content",t==="light"?"${THEME_COLOR.light}":"${THEME_COLOR.dark}");}catch(e){}})();`;

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>("dark");

  // Read the persisted choice once, after mount (the inline script already
  // painted it -- this just brings React's state in sync).
  React.useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "light" || stored === "dark") setThemeState(stored);
    } catch {
      // Storage unavailable (private mode, blocked) -- stay on the dark default.
    }
  }, []);

  const setTheme = React.useCallback((next: Theme) => {
    setThemeState(next);
    applyThemeClass(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Best-effort persistence; the toggle still works for the rest of the session.
    }
  }, []);

  const toggleTheme = React.useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [setTheme, theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider");
  return ctx;
}
