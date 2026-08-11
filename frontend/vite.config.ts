import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Pre-bundle every dependency at startup instead of letting the optimizer
  // discover them as modules are requested. Late discovery re-runs optimization
  // and re-stamps the `?v=` hash that source modules were already served with,
  // and the browser's now-stale import of `react-dom/client` comes back as a 504
  // "outdated optimize dep" -- which fails silently as a blank page, since the
  // only symptom is an unmounted root and a console error.
  optimizeDeps: {
    include: ["react", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime", "fflate"],
  },
  server: {
    port: 5173,
    // Proxying in dev keeps the browser same-origin, so CORS and cookie/token
    // handling behave the same locally as they do behind a reverse proxy.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
