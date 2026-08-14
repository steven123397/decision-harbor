import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:18080",
      "/ready": "http://127.0.0.1:18080",
      "/health": "http://127.0.0.1:18080",
    },
  },
});
