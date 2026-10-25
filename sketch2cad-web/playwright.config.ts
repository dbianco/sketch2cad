import { defineConfig, devices } from "@playwright/test";

// The E2E always runs against the compose stack of sketch2cad-api/ from the same commit
// (decision D-9), started by e2e/global-setup.ts; it never targets a static preview server.
const WEB_PORT = process.env.WEB_PORT ?? "18080";

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  forbidOnly: process.env.CI !== undefined,
  retries: process.env.CI !== undefined ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
