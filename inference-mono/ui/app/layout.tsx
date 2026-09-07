import type { Metadata, Viewport } from "next";

import "@/app/globals.css";
import { Providers } from "@/app/providers";
import { inter, sora } from "@/brand/fonts";

export const metadata: Metadata = {
  title: {
    default: "Talaris",
    template: "%s · Talaris"
  },
  description:
    "Open infrastructure for verifiable intelligence. Usage, billing, wallets, API keys, and inference.",
  icons: {
    icon: [{ url: "/brand/favicon.svg", type: "image/svg+xml" }]
  }
};

// Matches Void Black, so the browser chrome and any overscroll area stay in
// the ramp rather than flashing white.
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
      // The system is dark-only by rule -- there is no light product surface in
      // the brand kit -- so `dark` is pinned rather than toggled.
      className={`dark ${sora.variable} ${inter.variable}`}
      suppressHydrationWarning
    >
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
