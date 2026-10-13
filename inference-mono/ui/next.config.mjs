/** @type {import('next').NextConfig} */
// When chat runs on its own hostname, /chat is not a route here — send it (and any
// deep link under it) to that hostname instead of serving a 404. With a
// PUBLIC_BASE_PATH=/chat build fronted by the Worker, leave NEXT_PUBLIC_CHAT_URL
// unset and this redirect disappears.
const chatUrl = process.env.NEXT_PUBLIC_CHAT_URL;
const chatIsExternal = Boolean(chatUrl && /^https?:\/\//.test(chatUrl));
const uiDemoMode = process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_UI_DEMO === "true";

if (process.env.NEXT_PUBLIC_UI_DEMO === "true" && process.env.NODE_ENV === "production") {
  throw new Error("UI demo mode is for local development only. Unset NEXT_PUBLIC_UI_DEMO before building.");
}

const nextConfig = {
  reactStrictMode: true,
  experimental: { instrumentationHook: true },
  // Local-dev-only escape hatch for a root-owned `.next` left behind by an earlier
  // container run: `export NEXT_DIST_DIR=.next-rizzo` before `pnpm dev`/`pnpm build`
  // on a box where that happened. Must stay unset by default so production builds
  // (Docker or otherwise) always use Next's standard `.next` output — a build that
  // writes to a custom distDir but gets started assuming `.next` is how a host ends
  // up silently falling back to a stale/dev server.
  ...(uiDemoMode
    ? { distDir: ".next-demo", typescript: { tsconfigPath: "tsconfig.demo.json" } }
    : process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  // wagmi's connector bundle pulls in optional peers neither MetaMask's SDK
  // (React Native storage) nor WalletConnect's logger (pretty-printing) need
  // in a browser bundle -- webpack still tries to resolve them and warns.
  // Documented RainbowKit/wagmi + Next.js fix: https://www.rainbowkit.com/docs/installation
  webpack: (config, { webpack }) => {
    config.externals.push('pino-pretty', 'lokijs', 'encoding');
    // Scoped package name breaks webpack's string-externals form (it emits an
    // invalid bare-identifier reference), so the RN-only MetaMask SDK optional
    // dependency is dropped via IgnorePlugin instead.
    config.plugins.push(
      new webpack.IgnorePlugin({
        resourceRegExp: /^@react-native-async-storage\/async-storage$/
      })
    );
    return config;
  },
  async redirects() {
    if (uiDemoMode) return [{ source: '/chat/:path*', destination: '/dashboard', permanent: false }];
    if (!chatIsExternal) return [];
    const base = chatUrl.replace(/\/$/, '');
    return [
      { source: '/chat', destination: base, permanent: false },
      { source: '/chat/:path*', destination: `${base}/:path*`, permanent: false }
    ];
  }
};

export default nextConfig;
