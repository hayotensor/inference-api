import { test, expect } from "@playwright/test";

test("demo dashboard loads without wallet code, errors, or duplicate attestation checks", async ({ page, baseURL }) => {
  const errors: string[] = [];
  const external: string[] = [];
  const scripts: Promise<string>[] = [];
  let checks = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.request().resourceType() === "script") scripts.push(response.text());
  });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(baseURL!).origin) {
      external.push(url.href);
      return route.abort();
    }
    if (url.pathname === "/api/attestation-status") checks++;
    return route.continue();
  });
  await page.addInitScript(() => {
    localStorage.setItem("wagmi.store", "existing-wallet-session");
    localStorage.setItem("inference-api.auth.v1", "existing-real-session");
  });
  await page.goto("/login");
  await page.getByRole("button", { name: "Enter local demo", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByText("Starter", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Attestation unverified/ })).toBeVisible();
  expect(checks).toBe(1);
  await page.locator("aside").getByRole("link", { name: "Wallet", exact: true }).click();
  await expect(page.getByRole("button", { name: "Wallet connection unavailable in demo" })).toBeDisabled();
  await page.locator("aside").getByRole("link", { name: "Overview", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  expect(checks).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem("wagmi.store"))).toBe("existing-wallet-session");
  expect(await page.evaluate(() => localStorage.getItem("inference-api.auth.v1"))).toBe("existing-real-session");
  expect((await Promise.all(scripts)).join("\n")).not.toMatch(/WagmiProviderNotFoundError|@wagmi\/core|@rainbow-me\/rainbowkit/);
  expect(external).toEqual([]);
  expect(errors).toEqual([]);
});

test("attestation polls once, shares manual refresh, and clears a previous verification on failure", async ({ page }) => {
  let checks = 0;
  let fail = false;
  await page.clock.install();
  await page.route("**/api/attestation-status", (route) => {
    checks++;
    return route.fulfill({
      status: fail ? 503 : 200,
      json: { status: "verified", checkedAt: new Date().toISOString(), entryCount: 1 }
    });
  });
  await page.goto("/login");
  await page.getByRole("button", { name: "Enter local demo", exact: true }).click();
  await expect(page.locator("button").filter({ hasText: "Provably attested" })).toHaveCount(2);
  expect(checks).toBe(1);
  await page.clock.fastForward(6 * 60 * 60 * 1000 + 1000);
  await expect.poll(() => checks).toBe(2);
  await page.getByRole("button", { name: /Provably attested/ }).click();
  const refresh = page.getByRole("button", { name: "Check now", exact: true });
  await expect(refresh).toBeEnabled();
  fail = true;
  await refresh.click();
  await expect(page.getByRole("dialog")).toContainText("Attestation unverified");
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("button").filter({ hasText: "Attestation unverified" })).toHaveCount(2);
  expect(checks).toBe(3);
});
