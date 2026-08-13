import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiUpstream = process.env.API_UPSTREAM ?? "http://127.0.0.1:8000";

const proxy = {
  "/api": apiUpstream,
  "/health": apiUpstream,
  "/ready": apiUpstream,
};

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: {
    host: "0.0.0.0",
    port: 80,
    allowedHosts: true,
    proxy,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["e2e/**", "node_modules/**"],
  },
});
