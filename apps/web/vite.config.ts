import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const apiTarget = process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': apiTarget,
      '/health': apiTarget,
      '/ready': apiTarget,
    },
  },
  preview: {
    allowedHosts: true,
    proxy: {
      '/api': apiTarget,
      '/health': apiTarget,
      '/ready': apiTarget,
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
    css: true,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
