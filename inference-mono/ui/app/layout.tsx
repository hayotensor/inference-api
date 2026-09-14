import type { Metadata, Viewport } from "next";

import "@rainbow-me/rainbowkit/styles.css";
import "@/app/globals.css";
import { Providers } from "@/app/providers";
import { inter, sora } from "@/brand/fonts";
import { INLINE_SET_THEME_SCRIPT } from "@/lib/theme";

export const metadata: Metadata = {
  title: {
    default: "Talaris",
    template: "%s · Talaris"
  },
  description:
    "Open infrastructure for inference with receipts. Usage, billing, wallets, API keys, and inference.",
  icons: {
    icon: [{ url: "/brand/favicon.svg", type: "image/svg+xml" }]
  }
};

// Void Black by default so the browser chrome and any overscroll area stay in
// the ramp rather than flashing white; `lib/theme.tsx` repaints this meta tag
// to Off-White when the light toggle is on (its own effect, not this static
// default -- Next only renders this once).
export const viewport: Viewport = {
  themeColor: "#0A0A0B"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      // Dark is the default look -- unchanged from before light mode existed --
      // but no longer pinned: the inline script below swaps this to `light`
      // before paint if that's the persisted choice, and `lib/theme.tsx` keeps
      // it in sync after hydration.
      className={`dark ${sora.variable} ${inter.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: INLINE_SET_THEME_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
