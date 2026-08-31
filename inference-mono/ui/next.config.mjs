/** @type {import('next').NextConfig} */
// When chat runs on its own hostname, /chat is not a route here — send it (and any
// deep link under it) to that hostname instead of serving a 404. With a
// PUBLIC_BASE_PATH=/chat build fronted by the Worker, leave NEXT_PUBLIC_CHAT_URL
// unset and this redirect disappears.
const chatUrl = process.env.NEXT_PUBLIC_CHAT_URL;
const chatIsExternal = Boolean(chatUrl && /^https?:\/\//.test(chatUrl));

const nextConfig = {
  reactStrictMode: true,
  distDir: process.env.NEXT_DIST_DIR || '.next-rizzo',
  async redirects() {
    if (!chatIsExternal) return [];
    const base = chatUrl.replace(/\/$/, '');
    return [
      { source: '/chat', destination: base, permanent: false },
      { source: '/chat/:path*', destination: `${base}/:path*`, permanent: false }
    ];
  }
};

export default nextConfig;
