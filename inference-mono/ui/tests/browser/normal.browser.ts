import { test, expect, type Page } from "@playwright/test";
import { createDemoState } from "../../lib/demo/fixtures";

const address = "0x000000000000000000000000000000000000dEaD";

async function installWallet(page: Page) {
  await page.addInitScript((walletAddress) => {
    let connected = false;
    const calls: { method: string; params?: unknown[] }[] = [];
    const listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    const provider = {
      on(event: string, listener: (...args: unknown[]) => void) {
        if (!listeners.has(event)) listeners.set(event, new Set());
        listeners.get(event)!.add(listener);
      },
      removeListener(event: string, listener: (...args: unknown[]) => void) {
        listeners.get(event)?.delete(listener);
      },
      async request(request: { method: string; params?: unknown[] }) {
        calls.push(request);
        switch (request.method) {
          case "eth_chainId": return "0x1";
          case "eth_accounts": return connected ? [walletAddress] : [];
          case "eth_requestAccounts": connected = true; return [walletAddress];
          case "wallet_requestPermissions": connected = true; return [{ parentCapability: "eth_accounts" }];
          case "wallet_getPermissions": return connected ? [{ parentCapability: "eth_accounts" }] : [];
          case "personal_sign": return `0x${"11".repeat(65)}`;
          default: throw new Error(`Unexpected wallet method: ${request.method}`);
        }
      }
    };
    Reflect.set(window, "ethereum", provider);
    Reflect.set(window, "walletTestCalls", calls);
    window.addEventListener("eip6963:requestProvider", () => {
      window.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: {
        info: {
          uuid: "00000000-0000-4000-8000-000000000099",
          name: "Test Wallet", rdns: "test.wallet",
          icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>"
        },
        provider
      } }));
    });
  }, address);
}

async function mockBackend(page: Page, baseURL: string, signedIn: boolean) {
  const fixtures = createDemoState();
  let linked = false;
  const verifications: { path: string; body: Record<string, string> }[] = [];
  if (signedIn) {
    await page.addInitScript((user) => {
      localStorage.setItem("inference-api.auth.v1", JSON.stringify({
        user, accessToken: "browser-test-token", refreshToken: "browser-test-refresh",
        expiresAt: Date.now() + 3_600_000
      }));
    }, fixtures.user);
  }
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/sso-cookie") return route.fulfill({ status: 204 });
    if (path === "/api/attestation-status") return route.fulfill({ json: {
      status: "unverified", reason: "pin_not_configured", checkedAt: new Date().toISOString()
    } });
    if (["/auth/wallet/nonce", "/wallets/evm/nonce"].includes(path)) {
      return route.fulfill({ json: { address, message: "Browser test nonce", expires_at: new Date(Date.now() + 60_000).toISOString() } });
    }
    if (["/auth/wallet/verify", "/wallets/evm/verify"].includes(path)) {
      verifications.push({ path, body: route.request().postDataJSON() });
      linked = true;
      return route.fulfill({ json: path.startsWith("/auth/") ? {
        user: fixtures.user, access_token: "browser-test-token", refresh_token: "browser-test-refresh",
        token_type: "bearer", expires_in: 3600
      } : fixtures.wallet });
    }
    const responses: Record<string, unknown> = {
      "/me": fixtures.user, "/usage/current": fixtures.usage,
      "/billing/subscription": fixtures.subscription, "/api-keys": fixtures.keys,
      "/wallets/evm": linked ? fixtures.wallet : null
    };
    if (path in responses) return route.fulfill({ json: responses[path] });
    if (url.origin !== new URL(baseURL).origin) return route.abort();
    return route.continue();
  });
  return verifications;
}

test("dashboard defers wallet SDK until connection, then signs and shares the connection across routes", async ({ page, baseURL }) => {
  const errors: string[] = [];
  const scripts: Promise<string>[] = [];
  let collectingScripts = true;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (collectingScripts && response.request().resourceType() === "script") scripts.push(response.text());
  });
  await installWallet(page);
  const verifications = await mockBackend(page, baseURL!, true);
  const started = Date.now();
  await page.goto("/dashboard");
  await expect(page.getByText("Starter", { exact: true })).toBeVisible();
  console.log(`Dashboard ready with mocked backend: ${Date.now() - started} ms`);
  collectingScripts = false;
  expect((await Promise.all(scripts)).join("\n")).not.toMatch(/WagmiProviderNotFoundError|@wagmi\/core|@rainbow-me\/rainbowkit/);
  expect(await page.evaluate(() => Reflect.get(window, "walletTestCalls"))).toEqual([]);
  await page.getByRole("button", { name: "Connect wallet", exact: true }).click();
  await page.getByRole("button", { name: "Test Wallet", exact: true }).click();
  await page.getByRole("button", { name: "Verify & link", exact: true }).click();
  await expect(page.getByRole("link", { name: "Manage", exact: true })).toBeVisible();
  expect(verifications).toEqual([{ path: "/wallets/evm/verify", body: {
    address, message: "Browser test nonce", signature: `0x${"11".repeat(65)}`
  } }]);
  await page.locator("aside").getByRole("link", { name: "Wallet", exact: true }).click();
  await expect(page.getByRole("button", { name: /Switch wallet/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Verify", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: /Switch wallet/ }).click();
  await expect(page.getByRole("button", { name: /Disconnect/ })).toBeVisible();
  await page.getByRole("button", { name: /Disconnect/ }).click();
  await expect(page.getByRole("button", { name: "Connect wallet", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Verify", exact: true })).toBeDisabled();
  expect(errors).toEqual([]);
});

test("wallet login loads on demand and signs the backend nonce", async ({ page, baseURL }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await installWallet(page);
  const verifications = await mockBackend(page, baseURL!, false);
  await page.goto("/login");
  await page.getByRole("button", { name: "Sign in with wallet", exact: true }).click();
  await page.getByRole("button", { name: "Test Wallet", exact: true }).click();
  await page.getByRole("button", { name: /Sign in with wallet \(/ }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  expect(verifications).toEqual([{ path: "/auth/wallet/verify", body: {
    address, message: "Browser test nonce", signature: `0x${"11".repeat(65)}`
  } }]);
  expect(errors).toEqual([]);
});
