import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 浏览器只访问 web 端口；/api 与 /health 代理到 API 服务（见 docs/design/workbench.md）
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Compose 网络内浏览器测试以服务名访问（见 docs/design/local-runtime.md）
    allowedHosts: ["web"],
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
      "/health": { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
  },
});
