import { sentryVitePlugin } from "@sentry/vite-plugin";
import tailwindcss from "@tailwindcss/vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig, loadEnv, type PluginOption } from "vite";

// VITE_API_TARGET — куди proxy'ять /api, /mcp, /health.
// Default — codex-server:8000 (всередині docker network) для HMR-сервісу
// у compose. Якщо запускаєш `npm run dev` на host'і — переопредели на
// http://localhost:8088.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiTarget = env.VITE_API_TARGET ?? "http://codex-server:8000";

  const plugins: PluginOption[] = [svelte(), tailwindcss()];
  // Source maps → Bugsink через Sentry-CLI protocol. Build-only: Sentry-CLI
  // потребує BUGSINK_INTERNAL_URL + BUGSINK_AUTH_TOKEN + org/project slug.
  // Empty token → plugin no-op (logs warning, не падає білд).
  if (env.SENTRY_AUTH_TOKEN && env.SENTRY_ORG && env.SENTRY_PROJECT && env.SENTRY_URL) {
    plugins.push(
      sentryVitePlugin({
        url: env.SENTRY_URL,
        authToken: env.SENTRY_AUTH_TOKEN,
        org: env.SENTRY_ORG,
        project: env.SENTRY_PROJECT,
        release: { name: env.VITE_SENTRY_RELEASE || undefined },
        sourcemaps: { filesToDeleteAfterUpload: ["dist/**/*.map"] }
      })
    );
  }

  return {
    plugins,
    build: {
      sourcemap: "hidden"
    },
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
