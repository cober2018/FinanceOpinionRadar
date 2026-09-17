/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// API 代理：默认 make dev 的 :8000；本机端口被占时 API_PORT=8010 make web
const apiTarget = `http://localhost:${process.env.API_PORT ?? 8000}`

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
  },
})
