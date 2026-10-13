import { defineConfig } from "@playwright/test";

// API adapter tests only: no webServer or browser fixtures, and no running UI.
export default defineConfig({
  testDir: "./tests",
  projects: [
    { name: "demo", testMatch: "demo.spec.ts" },
    { name: "normal", testMatch: "normal.spec.ts" },
    { name: "production", testMatch: "normal.spec.ts" }
  ],
  workers: 1,
  reporter: "list",
  outputDir: "/tmp/talaris-ui-demo-tests"
});
