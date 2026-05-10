import tailwindcss from "@tailwindcss/vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig, loadEnv } from "vite";

// VITE_API_TARGET — куди proxy'ять /api, /mcp, /health.
// Default — codex-server:8000 (всередині docker network) для HMR-сервісу
// у compose. Якщо запускаєш `npm run dev` на host'і — переопредели на
// http://localhost:8088.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.VITE_API_TARGET ?? "http://codex-server:8000";

  return {
    plugins: [svelte(), tailwindcss()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      watch: {
        // Bind-mount у docker іноді fsnotify не працює — fallback на polling.
        usePolling: true,
        interval: 500
      },
      proxy: {
        "/api": apiTarget,
        "/health": apiTarget,
        "/mcp": apiTarget,
        "/generated": apiTarget,
        "/minio": {
          target: env.VITE_MINIO_TARGET ?? "http://minio:9000",
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/minio/, "")
        }
      }
    }
  };
});
