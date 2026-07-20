import { defineConfig } from "@playwright/test";

const baseURL = process.env.WEB_BASE_URL || "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL,
    headless: true,
  },
});
