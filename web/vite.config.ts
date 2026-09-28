import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// The dev server proxies instead of relying on CORS. FastAPI blocks a browser
// origin twice -- `reject_untrusted_browser_origin` 403s every path and
// CORSMiddleware is only registered when CORS_ALLOWED_ORIGINS is set, which it
// never is. A proxied request arrives server-side with no Origin header, which
// `is_browser_origin_allowed` explicitly permits, so neither block applies and
// the production setup (same origin) stays the only supported one.
const API = process.env.MPT_API_URL ?? "http://127.0.0.1:8080"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: false },
      "/tasks": { target: API, changeOrigin: false },
    },
  },
})
