import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: process.env.WEB_BASE_URL ?? "http://127.0.0.1:15173",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
