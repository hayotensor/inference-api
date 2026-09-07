// Talaris brand typefaces, self-hosted.
//
// Sora carries display + headings, Inter carries body + UI — the two families
// named by the Talaris brand kit. Both are the variable builds shipped in the
// design system's `fonts/`, loaded through next/font/local so they are
// subset, preloaded, and served from our own origin (no Google Fonts request,
// no layout shift). The exposed CSS variables are consumed by
// tailwind.config.ts (`font-display` / `font-body`) and app/globals.css.
import localFont from "next/font/local";

export const sora = localFont({
  src: "./fonts/Sora-VariableFont_wght.ttf",
  weight: "100 900",
  style: "normal",
  display: "swap",
  variable: "--font-display",
  fallback: ["Inter", "system-ui", "sans-serif"]
});

export const inter = localFont({
  src: "./fonts/Inter-VariableFont_opsz_wght.ttf",
  weight: "100 900",
  style: "normal",
  display: "swap",
  variable: "--font-body",
  fallback: ["system-ui", "sans-serif"]
});
