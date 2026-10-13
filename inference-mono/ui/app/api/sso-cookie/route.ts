import { NextRequest, NextResponse } from "next/server";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

// Server-side: the browser's SSO cookie must be httpOnly so the edge Worker (not
// JS) reads it. This handler exchanges the caller's access token for a signed SSO
// token from the API and sets it as an httpOnly cookie on the app domain.

const API_BASE_URL =
  process.env.INTERNAL_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

const COOKIE_NAME = process.env.SSO_COOKIE_NAME ?? "talaris_session";

// Host-only by default. Set SSO_COOKIE_DOMAIN (e.g. ".example.com") only when chat
// lives on a SIBLING hostname rather than a path on this one: a parent-domain
// cookie is sent to every subdomain of that parent, so keep the scope as tight as
// the layout allows. Unset it whenever /chat is served from this origin.
const COOKIE_DOMAIN = process.env.SSO_COOKIE_DOMAIN?.trim() || undefined;

// The cookie travels to another origin when it is domain-scoped, so it must be
// Secure there even while the dashboard itself runs a dev server over plain HTTP.
const COOKIE_SECURE = process.env.NODE_ENV === "production" || Boolean(COOKIE_DOMAIN);

function cookieOptions(maxAge: number) {
  return {
    name: COOKIE_NAME,
    httpOnly: true,
    secure: COOKIE_SECURE,
    sameSite: "lax" as const,
    path: "/",
    maxAge,
    ...(COOKIE_DOMAIN ? { domain: COOKIE_DOMAIN } : {})
  };
}

export async function POST(request: NextRequest) {
  // Cookies are shared across localhost ports. Demo must neither mint nor
  // expire the SSO cookie used by a normal UI running alongside it.
  if (UI_DEMO_MODE) return new NextResponse(null, { status: 204 });
  const authorization = request.headers.get("authorization");
  if (!authorization) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const upstream = await fetch(`${API_BASE_URL.replace(/\/$/, "")}/auth/sso/cookie`, {
    method: "POST",
    headers: { Authorization: authorization }
  });
  if (!upstream.ok) {
    return NextResponse.json({ error: "sso_failed" }, { status: upstream.status });
  }

  const data = (await upstream.json()) as { sso_token: string; expires_in: number };
  const response = new NextResponse(null, { status: 204 });
  response.cookies.set({ ...cookieOptions(data.expires_in), value: data.sso_token });
  return response;
}

export async function DELETE() {
  if (UI_DEMO_MODE) return new NextResponse(null, { status: 204 });
  const response = new NextResponse(null, { status: 204 });
  response.cookies.set({ ...cookieOptions(0), value: "" });
  if (COOKIE_DOMAIN) {
    // A cookie set before SSO_COOKIE_DOMAIN was configured is host-only, and a
    // domain-scoped expiry does not clear it — the browser would keep sending a
    // stale session alongside the new one. Expire that variant too.
    const { domain: _domain, ...hostOnly } = cookieOptions(0);
    response.cookies.set({ ...hostOnly, value: "" });
  }
  return response;
}
