import { defineConfig } from "@playwright/test";

// Attach to an existing local UI. Tests mock backend/wallet responses and
// never require a real account, wallet, or deployed service.
export default defineConfig({
  testDir: "./tests/browser",
  testMatch: process.env.PLAYWRIGHT_UI_MODE === "normal" ? "normal.browser.ts" : "demo.browser.ts",
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001", viewport: { width: 1440, height: 1000 } },
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: "list",
  outputDir: "/tmp/talaris-ui-browser-tests"
});
