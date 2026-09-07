import type { Config } from "tailwindcss";

/**
 * Talaris Design System bridge.
 *
 * Colors resolve to the tokens defined in app/globals.css. The radius, shadow,
 * font, and motion scales are pinned here rather than applied per-component so
 * the brand rules hold for every `rounded-*` / `shadow-*` / `duration-*` in the
 * app, including ones added later:
 *
 *   - Radii collapse to the kit's 2 / 4 / 8 (pills / cards / panels max).
 *     Nothing is sharp-cornered, nothing above 8px, `rounded-full` survives
 *     only for genuinely circular things (spinners, status dots).
 *   - Every drop shadow is `none`. Elevation is tone + border, never blur.
 *   - Durations and easing are the kit's calm 120/200/320 on a single curve.
 */
const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        "border-strong": "hsl(var(--border-strong))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        elevated: "hsl(var(--elevated))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
          hover: "hsl(var(--primary-hover))"
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))"
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))"
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))"
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))"
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))"
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))"
        },
        // Raw ramp, for the rare place that needs a named step rather than a
        // role (the monolith underlay, the grid rule, a disabled glyph).
        void: "hsl(var(--void-black))",
        onyx: "hsl(var(--onyx))",
        charcoal: "hsl(var(--charcoal))",
        graphite: "hsl(var(--graphite))",
        stone: "hsl(var(--stone-gray))",
        warm: "hsl(var(--warm-gray))",
        eggshell: "hsl(var(--eggshell))",
        offwhite: "hsl(var(--off-white))"
      },
      fontFamily: {
        display: ["var(--font-display)", "var(--font-body)", "system-ui", "sans-serif"],
        sans: ["var(--font-body)", "system-ui", "sans-serif"],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "SF Mono",
          "Menlo",
          "Roboto Mono",
          "monospace"
        ]
      },
      letterSpacing: {
        label: "0.14em",
        display: "-0.01em"
      },
      borderRadius: {
        none: "0",
        sm: "2px",
        DEFAULT: "2px",
        md: "2px",
        lg: "4px",
        xl: "4px",
        "2xl": "8px",
        "3xl": "8px",
        full: "9999px"
      },
      boxShadow: {
        none: "none",
        sm: "none",
        DEFAULT: "none",
        md: "none",
        lg: "none",
        xl: "none",
        "2xl": "none",
        inner: "none"
      },
      transitionTimingFunction: {
        DEFAULT: "cubic-bezier(0.2, 0, 0, 1)",
        standard: "cubic-bezier(0.2, 0, 0, 1)"
      },
      transitionDuration: {
        DEFAULT: "200ms",
        fast: "120ms",
        base: "200ms",
        slow: "320ms"
      }
    }
  },
  plugins: []
};

export default config;
