import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Mirrors /Users/zain/CPM_APP/frontend/vite.config.js — Vite proxies every
// `/api/*` request from the dev server (5173) to the FastAPI backend (8000)
// so the SPA can use relative URLs in both dev and production.
// Port defaults are QTO-specific (8042 backend, 5142 frontend) so they
// coexist with the CPM dev stack on a single dev machine. The launcher
// script overrides via VITE_API_URL + VITE_PORT.
export default defineConfig({
  plugins: [react()],
  server: {
    port: Number(process.env.VITE_PORT) || 5142,
    host: true,
    proxy: {
      '/api': process.env.VITE_API_URL || 'http://127.0.0.1:8042',
    },
  },
})
