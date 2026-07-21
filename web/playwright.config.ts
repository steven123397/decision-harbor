import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  expect: { timeout: 15000 },
  retries: 0,
  use: {
    baseURL: `http://localhost:${process.env.DH_WEB_PORT || 5173}`,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
