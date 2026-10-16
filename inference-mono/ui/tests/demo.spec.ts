import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import {
  adminApi, apiKeysApi, apiRequest, authApi, billingApi, clearSsoCookie,
  referralsApi, redirectToStripeSession, syncSsoCookie, usageApi, userApi, walletsApi
} from "../lib/api";
import { authStore, AUTH_STORAGE_KEY, tokenResponseToSession } from "../lib/auth-store";
import { DEMO_EMAIL, DEMO_PASSWORD } from "../lib/demo-mode";
import { resetDemoState } from "../lib/demo/api";
import { loadNextConfig } from "./next-config";

const originalFetch = globalThis.fetch;
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
let networkRequests: string[] = [];

test.beforeEach(async () => {
  resetDemoState();
  authStore.clear();
  const storage = new Map<string, string>();
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
        removeItem: (key: string) => storage.delete(key)
      },
      location: { origin: "http://localhost:3001" },
      dispatchEvent: () => true
    }
  });
  networkRequests = [];
  globalThis.fetch = async (input) => {
    networkRequests.push(String(input));
    throw new Error("Demo must not use the network");
  };
});

test.afterEach(() => {
  globalThis.fetch = originalFetch;
  authStore.clear();
  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else Reflect.deleteProperty(globalThis, "window");
  expect(networkRequests).toEqual([]);
});

async function login() {
  const response = await authApi.login({ email: DEMO_EMAIL, password: DEMO_PASSWORD });
  authStore.write(tokenResponseToSession(response));
  return response.user;
}

test("login and owner access work without a backend, including expired sessions and SSO", async () => {
  const realSession = JSON.stringify({ accessToken: "real-token", refreshToken: "real-refresh", user: { id: "real-user" } });
  window.localStorage.setItem("inference-api.auth.v1", realSession);
  await expect(userApi.me()).rejects.toMatchObject({ status: 401 });
  await expect(authApi.login({ email: "wrong@example.com", password: "wrong" })).rejects.toMatchObject({ status: 401 });
  const user = await login();
  expect(AUTH_STORAGE_KEY).toBe("inference-api.demo-auth.v1");
  expect(await userApi.me()).toEqual(user);
  expect((await adminApi.me()).permissions).toContain("admins.owner.write");
  authStore.write({ ...authStore.read()!, expiresAt: 0 });
  expect((await userApi.me()).id).toBe(user.id);
  await syncSsoCookie();
  await clearSsoCookie();
  await authApi.logout(authStore.refreshToken());
  authStore.clear();
  expect(window.localStorage.getItem("inference-api.auth.v1")).toBe(realSession);
  await expect(adminApi.me()).rejects.toMatchObject({ status: 401 });
});

test("every dashboard and admin read has sample data", async () => {
  const user = await login();
  const results = await Promise.all([
    usageApi.current(), usageApi.allowance(), usageApi.history(), usageApi.byModel(),
    billingApi.plans(), billingApi.subscription(), billingApi.walletBalance(), billingApi.walletBundles(),
    referralsApi.me(), walletsApi.get(), apiKeysApi.list(), apiKeysApi.availableModels(),
    adminApi.roles(), adminApi.users(), adminApi.user(user.id), adminApi.apiKeys(),
    adminApi.billingPlans(), adminApi.auditLogs(), adminApi.statsOverview(), adminApi.statsUsage(),
    adminApi.statsBilling(), adminApi.userApiKeys(user.id), adminApi.userBilling(user.id),
    adminApi.userUsage(user.id), adminApi.userWallet(user.id)
  ]);
  expect(results.every((result) => result !== undefined && result !== null)).toBe(true);
  const models = await usageApi.byModel();
  const detail = await usageApi.byModelDetail(models.data[0].model, { limit: 3 });
  expect(detail.events).toHaveLength(3);
  expect(detail.summary.request_count).toBe(detail.total_events);
  const events = (await usageApi.history()).data;
  expect(events.reduce((total, event) => total + event.charged_tokens, 0)).toBe((await usageApi.current()).used_tokens);
});

test("profile and API-key edits update fixtures without mutating previous responses", async () => {
  await login();
  const before = await userApi.me();
  await userApi.update({ full_name: "UI Developer" });
  expect((await userApi.me()).full_name).toBe("UI Developer");
  expect(before.full_name).toBe("Local Developer");
  const created = await apiKeysApi.create({ name: "UI test key", environment: "test", scopes: ["models:read"] });
  expect(created.key).toContain("not_a_real_key");
  await apiKeysApi.update(created.id, { name: "Renamed UI key" });
  expect((await apiKeysApi.list()).find((key) => key.id === created.id)?.name).toBe("Renamed UI key");
  await apiKeysApi.revoke(created.id);
  expect((await apiKeysApi.list()).find((key) => key.id === created.id)?.revoked_at).toBeTruthy();
  resetDemoState();
  expect((await apiKeysApi.list()).some((key) => key.id === created.id)).toBe(false);
});

test("billing, wallet, and admin user mutations are local and queries support filtering", async () => {
  await login();
  const plan = (await billingApi.plans()).find((item) => item.name === "Pro")!;
  expect((await billingApi.changePlan(plan.id, "year")).amount_cents).toBe(plan.annual_price_cents);
  expect((await usageApi.current()).stripe_allowance).toBe(plan.monthly_token_allowance);
  expect((await billingApi.cancel()).cancel_at_period_end).toBe(true);
  await walletsApi.syncBalances();
  await walletsApi.unlink();
  expect(await walletsApi.get()).toBeNull();
  const users = await adminApi.users({ search: "Alex", limit: 1 });
  expect(users.total).toBe(1);
  await adminApi.disableUser(users.data[0].id);
  expect((await adminApi.user(users.data[0].id)).is_active).toBe(false);
  expect((await adminApi.users({ status: "disabled" })).total).toBe(2);
});

test("external and unknown operations fail locally and cancellation is honored", async () => {
  await login();
  await expect(billingApi.portal()).rejects.toMatchObject({ status: 501 });
  await expect(billingApi.walletCheckout({ bundle_id: "topup_10" })).rejects.toMatchObject({ status: 501 });
  await expect(apiRequest("/new-unimplemented-route")).rejects.toMatchObject({ status: 501 });
  expect(authApi.oauthLoginUrl("google")).toBe("/login");
  expect(() => redirectToStripeSession({ url: "https://checkout.example.com" })).toThrow("Stripe connections are disabled");
  const controller = new AbortController();
  controller.abort();
  await expect(userApi.me({ signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
});

test("demo flag is opt-in and cannot enable demo mode in production", () => {
  const source = ts.transpileModule(readFileSync("lib/demo-mode.ts", "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS }
  }).outputText;
  for (const [nodeEnv, flag, expected] of [
    ["development", "true", true], ["development", undefined, false],
    ["production", "true", false], ["test", "true", false]
  ] as const) {
    const sandbox = { exports: {} as { UI_DEMO_MODE: boolean }, process: { env: { NODE_ENV: nodeEnv, NEXT_PUBLIC_UI_DEMO: flag } } };
    runInNewContext(source, sandbox);
    expect(sandbox.exports.UI_DEMO_MODE).toBe(expected);
  }
});

test("production config rejects the demo flag without starting Next.js", () => {
  expect(() => loadNextConfig({ NODE_ENV: "production", NEXT_PUBLIC_UI_DEMO: "true" }))
    .toThrow("UI demo mode is for local development only");
});

test("referral URLs follow the current demo origin and custom port", async () => {
  await login();
  expect((await referralsApi.me()).referral_link).toBe("http://localhost:3001/signup?ref=LOCALDEMO");
});

test("server routes cannot contact the backend or alter a real SSO cookie in demo mode", async () => {
  const { NextRequest } = require("next/server") as typeof import("next/server");
  const sso = require("../app/api/sso-cookie/route") as typeof import("../app/api/sso-cookie/route");
  const request = new NextRequest("http://localhost:3001/api/sso-cookie", {
    method: "POST", headers: { Authorization: "Bearer local-demo-access", Cookie: "talaris_session=real-session" }
  });
  for (const response of [await sso.POST(request), await sso.DELETE()]) {
    expect(response.status).toBe(204);
    expect(response.headers.has("set-cookie")).toBe(false);
  }
  const attestation = require("../app/api/attestation-status/route") as typeof import("../app/api/attestation-status/route");
  expect(await (await attestation.GET()).json()).toMatchObject({ status: "unverified", reason: "local_demo" });
  const inference = require("../app/api/guest-inference/route") as typeof import("../app/api/guest-inference/route");
  expect((await inference.POST(new Request("http://localhost:3001/api/guest-inference", {
    method: "POST", headers: { "X-Guest-Id": "demo" }, body: JSON.stringify({ prompt: "Hello" })
  }))).status).toBe(501);
  const status = require("../app/api/status/route") as typeof import("../app/api/status/route");
  expect((await (await status.GET()).json()).groups[0].name).toBe("Demo services");
  const { ensureSampler } = require("../lib/server/status-history") as typeof import("../lib/server/status-history");
  await ensureSampler();
});

test("demo wallet configuration cannot read or write the normal wallet store", async () => {
  // Import as on the server; wagmi must not allocate any persistent storage.
  Reflect.deleteProperty(globalThis, "window");
  const { wagmiConfig } = require("../lib/wagmi") as typeof import("../lib/wagmi");
  expect(wagmiConfig.storage).toBeNull();
  expect(wagmiConfig.connectors).toEqual([]);
  await expect(wagmiConfig.getClient().request({ method: "eth_blockNumber" }))
    .rejects.toThrow("Blockchain connections are disabled");
});

test("demo refuses to load the wallet SDK", async () => {
  const { loadWalletRuntime } = require("../lib/wallet-loader") as typeof import("../lib/wallet-loader");
  await expect(loadWalletRuntime()).rejects.toThrow("Wallet connections are disabled");
});

test("demo chat and build outputs stay isolated with real services configured", async () => {
  const config = loadNextConfig({
    NODE_ENV: "development", NEXT_PUBLIC_UI_DEMO: "true", NEXT_DIST_DIR: ".next", NEXT_PUBLIC_CHAT_URL: "https://chat.example.com"
  });
  expect(await config.redirects!()).toEqual([{ source: "/chat/:path*", destination: "/dashboard", permanent: false }]);
  expect(config.distDir).toBe(".next-demo");
  expect(config.typescript?.tsconfigPath).toBe("tsconfig.demo.json");
});
