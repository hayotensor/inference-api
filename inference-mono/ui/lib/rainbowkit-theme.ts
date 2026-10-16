import { darkTheme, lightTheme, type Theme } from "@rainbow-me/rainbowkit";

/**
 * RainbowKit's default theme is a blue/gradient look that clashes with the
 * Talaris neutral surfaces and Eggshell primary actions. These mirror the
 * `--primary` swap so the connect modal reads as part of the same product.
 * Semantic accent colors are reserved for status indicators.
 */
export const rainbowKitDarkTheme: Theme = darkTheme({
  accentColor: "#EDE8E7", // --eggshell, the dark-mode primary fill
  accentColorForeground: "#0A0A0B", // --void-black
  borderRadius: "medium",
  overlayBlur: "small"
});

export const rainbowKitLightTheme: Theme = lightTheme({
  accentColor: "#0A0A0B", // --void-black, the light-mode primary fill
  accentColorForeground: "#F7F6F4", // --off-white
  borderRadius: "medium",
  overlayBlur: "small"
});
