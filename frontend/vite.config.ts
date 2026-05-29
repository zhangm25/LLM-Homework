import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `envDir: ".."` is resolved relative to the project root (this folder), i.e.
// the repo root — so the single root `.env` is the only place keys live. Vite
// only exposes `VITE_*` vars to the client, so the LLM / AMap web-service
// keys in that same file stay server-side.
export default defineConfig({
  plugins: [react()],
  envDir: "..",
  // host: true -> listen on 0.0.0.0 so the dev server is reachable from other
  // machines (your MacBook) by the server's IP, not just localhost.
  // proxy /api -> backend: the page fetches its OWN origin (:5173) and Vite
  // forwards to the backend, so only :5173 needs to be reachable (works the
  // same over an SSH tunnel or a direct IP, no second port to open).
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // SSE (/api/chat) must stream, not buffer.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            proxyRes.headers["cache-control"] = "no-cache";
            proxyRes.headers["x-accel-buffering"] = "no";
          });
        },
      },
    },
  },
});
