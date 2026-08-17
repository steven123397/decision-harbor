import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // 上限覆盖自动重跑后的长查询用例（3 次执行 × 10s 语句超时 + 调度
  // 余量，ADR-0019）；各用例仍可用自己的 timeout 覆写此默认值。
  timeout: 120_000,
  retries: 0,
  use: {
    baseURL: process.env.WEB_URL ?? "http://localhost:8080",
  },
  reporter: [["list"]],
});
