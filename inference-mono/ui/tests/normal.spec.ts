import { test, expect } from "@playwright/test";
import type { TokenResponse, User } from "../lib/types";
import { loadNextConfig } from "./next-config";

// Each project runs in its own worker so module-level environment constants
// are evaluated afresh. No server or browser is started by these tests.
let api: typeof import("../lib/api");
let store: typeof import("../lib/auth-store");
const originalFetch = globalThis.fetch;
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
let requests: { url: string; init?: RequestInit }[];

const user: User = {
  id: "real-user", email: "real@example.com", full_name: "Backend User",
  is_active: true, is_verified: true, is_superuser: false,
  phone_number: null, phone_verified_at: null, created_at: "2026-01-01T00:00:00Z",
  oauth_providers: [], has_verifiable_email: true
};
const tokenResponse: TokenResponse = {
  access_token: "backend-token", refresh_token: "backend-refresh", token_type: "bearer",
  expires_in: 900, user
};

test.beforeAll(async () => {
  Object.assign(process.env, {
    NODE_ENV: test.info().project.name === "production" ? "production" : "development",
    // Even an accidentally set demo flag must not change production clients.
    NEXT_PUBLIC_UI_DEMO: test.info().project.name === "production" ? "true" : "false",
    NEXT_PUBLIC_API_BASE_URL: "http://api.example.invalid",
    INTERNAL_API_BASE_URL: "http://internal-api.example.invalid",
    INTERNAL_INFERENCE_API_BASE_URL: "http://inference.example.invalid",
    NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID: "",
    PLATFORM_PUBKEY: "1".repeat(64)
  });
  api = await import("../lib/api");
  store = await import("../lib/auth-store");
});

test.beforeEach(() => {
  store.authStore.clear();
  const storage = new Map<string, string>();
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
        removeItem: (key: string) => storage.delete(key)
      },
      dispatchEvent: () => true
    }
  });
  requests = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ url: String(input), init });
    const url = String(input);
    if (url.endsWith("/auth/login") || url.endsWith("/auth/refresh")) return Response.json(tokenResponse);
    if (url.endsWith("/auth/sso/cookie")) return Response.json({ sso_token: "backend-sso", expires_in: 3600 });
    if (url.endsWith("/me")) return Response.json(user);
    if (url.endsWith("/api/sso-cookie")) return new Response(null, { status: 204 });
    return Response.json({ backend: true });
  };
});

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  store.authStore.clear();
  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else Reflect.deleteProperty(globalThis, "window");
});

test("normal mode ignores demo sessions and preserves real login and token refresh", async () => {
  const demoSession = JSON.stringify({ accessToken: "local-demo-access", refreshToken: "local-demo-refresh", user: { id: "demo" } });
  window.localStorage.setItem("inference-api.demo-auth.v1", demoSession);
  expect(store.AUTH_STORAGE_KEY).toBe("inference-api.auth.v1");
  expect(store.authStore.read()).toBeNull();
  await expect(api.userApi.me()).rejects.toMatchObject({ status: 401 });
  expect(requests).toEqual([]);

  const response = await api.authApi.login({ email: user.email, password: "backend-password" });
  store.authStore.write(store.tokenResponseToSession(response));
  expect(requests[0].url).toBe("http://api.example.invalid/auth/login");
  expect(await api.userApi.me()).toEqual(user);
  expect(new Headers(requests.at(-1)?.init?.headers).get("Authorization")).toBe("Bearer backend-token");
  store.authStore.write({ ...store.authStore.read()!, expiresAt: 0 });
  await api.userApi.me();
  expect(requests.slice(-2).map((request) => request.url)).toEqual([
    "http://api.example.invalid/auth/refresh", "http://api.example.invalid/me"
  ]);
  store.authStore.clear();
  expect(window.localStorage.getItem("inference-api.demo-auth.v1")).toBe(demoSession);
});

test("backend errors are not replaced by demo results", async () => {
  globalThis.fetch = async () => Response.json({ detail: { code: "backend_denied", message: "Backend denied" } }, { status: 403 });
  await expect(api.authApi.login({ email: "dev@example.com", password: "local-demo" }))
    .rejects.toMatchObject({ status: 403, code: "backend_denied", message: "Backend denied" });
  const { demoRequest } = await import("../lib/demo/api");
  expect(() => demoRequest("/me")).toThrow("Local UI demo mode is disabled");
});

test("wallet checkout sends the selected bundle or custom amount to the backend", async () => {
  store.authStore.write(store.tokenResponseToSession(tokenResponse));
  await api.billingApi.walletCheckout({ bundle_id: "topup_25" });
  await api.billingApi.walletCheckout({ usd_cents: 3750 });
  expect(requests.map((request) => ({
    url: request.url,
    method: request.init?.method,
    body: JSON.parse(String(request.init?.body)),
    authorization: new Headers(request.init?.headers).get("Authorization")
  }))).toEqual([
    {
      url: "http://api.example.invalid/billing/wallet/checkout-session",
      method: "POST", body: { bundle_id: "topup_25" }, authorization: "Bearer backend-token"
    },
    {
      url: "http://api.example.invalid/billing/wallet/checkout-session",
      method: "POST", body: { usd_cents: 3750 }, authorization: "Bearer backend-token"
    }
  ]);
});

test("normal SSO calls and server cookie behavior are preserved", async () => {
  expect(api.authApi.oauthLoginUrl("google")).toBe("http://api.example.invalid/auth/google/login");
  const destinations: string[] = [];
  Object.defineProperty(window, "location", { value: { assign: (url: string) => destinations.push(url) } });
  api.redirectToStripeSession({ url: "https://checkout.example.com" });
  expect(destinations).toEqual(["https://checkout.example.com"]);
  store.authStore.write(store.tokenResponseToSession(tokenResponse));
  await api.syncSsoCookie();
  await api.clearSsoCookie();
  expect(requests.map((request) => [request.url, request.init?.method])).toEqual([
    ["/api/sso-cookie", "POST"], ["/api/sso-cookie", "DELETE"]
  ]);
  const { NextRequest } = require("next/server") as typeof import("next/server");
  const sso = require("../app/api/sso-cookie/route") as typeof import("../app/api/sso-cookie/route");
  expect((await sso.POST(new NextRequest("http://localhost/api/sso-cookie", { method: "POST" }))).status).toBe(401);
  const result = await sso.POST(new NextRequest("http://localhost/api/sso-cookie", {
    method: "POST", headers: { Authorization: "Bearer backend-token" }
  }));
  expect(requests.at(-1)?.url).toBe("http://internal-api.example.invalid/auth/sso/cookie");
  expect(result.headers.get("set-cookie")).toContain("backend-sso");
  expect((await sso.DELETE()).headers.get("set-cookie")).toContain("Max-Age=0");
});

test("normal inference and attestation still call their configured backends", async () => {
  const inference = require("../app/api/guest-inference/route") as typeof import("../app/api/guest-inference/route");
  const response = await inference.POST(new Request("http://localhost/api/guest-inference", {
    method: "POST", headers: { "X-Guest-Id": "guest" }, body: JSON.stringify({ prompt: "Hello" })
  }));
  expect(await response.json()).toEqual({ backend: true });
  expect(requests.at(-1)?.url).toBe("http://inference.example.invalid/v1/guest/inference");
  const attestation = require("../app/api/attestation-status/route") as typeof import("../app/api/attestation-status/route");
  expect((await (await attestation.GET()).json()).reason).toBe("malformed_response");
  expect(requests.slice(-2).map((request) => request.url)).toEqual([
    "http://inference.example.invalid/attest", "http://inference.example.invalid/allowlist"
  ]);
});

test("normal wallet configuration keeps storage and connectors enabled", async () => {
  Reflect.deleteProperty(globalThis, "window");
  const { wagmiConfig } = require("../lib/wagmi") as typeof import("../lib/wagmi");
  expect(wagmiConfig.storage).not.toBeNull();
  expect(wagmiConfig.connectors.length).toBeGreaterThan(0);
  expect(wagmiConfig.getClient().transport.type).toBe("http");
});

test("normal Next config retains the configured chat redirect and build directory", async () => {
  const config = loadNextConfig({
    NODE_ENV: process.env.NODE_ENV, NEXT_PUBLIC_UI_DEMO: "false", NEXT_PUBLIC_CHAT_URL: "https://chat.example.com"
  });
  expect(config.distDir).toBeUndefined();
  expect(config.typescript).toBeUndefined();
  expect(await config.redirects!()).toEqual([
    { source: "/chat", destination: "https://chat.example.com", permanent: false },
    { source: "/chat/:path*", destination: "https://chat.example.com/:path*", permanent: false }
  ]);
  expect(loadNextConfig({ NODE_ENV: process.env.NODE_ENV, NEXT_DIST_DIR: ".next-custom" }).distDir).toBe(".next-custom");
});
