import { darkTheme, lightTheme, type Theme } from "@rainbow-me/rainbowkit";

/**
 * RainbowKit's default theme is a blue/gradient look that clashes with the
 * Talaris brand kit, which is deliberately monochrome (see app/globals.css's
 * header comment: one ramp from Void Black to Off-White, Eggshell as the
 * only accent, no chromatic or status hues). These mirror the kit's
 * `--primary` swap so the connect modal reads as part of the same product
 * instead of a bolted-on widget.
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
