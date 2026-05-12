import "./styles/index.css";

import * as Sentry from "@sentry/svelte";
import { mount } from "svelte";

import App from "./App.svelte";

const SENSITIVE_HEADERS = new Set([
  "authorization",
  "cookie",
  "set-cookie",
  "x-api-key",
  "x-auth-token",
  "proxy-authorization"
]);

function scrubHeaders(headers: Record<string, unknown> | undefined): void {
  if (!headers) return;
  for (const key of Object.keys(headers)) {
    if (SENSITIVE_HEADERS.has(key.toLowerCase())) {
      headers[key] = "[redacted]";
    }
  }
}

if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    release: import.meta.env.VITE_SENTRY_RELEASE || undefined,
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? 0),
    sendDefaultPii: false,
    sendClientReports: false,
    beforeSend(event) {
      scrubHeaders(event.request?.headers as Record<string, unknown> | undefined);
      scrubHeaders(event.request?.cookies as Record<string, unknown> | undefined);
      if (event.request && "data" in event.request) {
        event.request.data = "[redacted]";
      }
      for (const breadcrumb of event.breadcrumbs ?? []) {
        scrubHeaders(breadcrumb.data as Record<string, unknown> | undefined);
      }
      return event;
    }
  });
}

mount(App, {
  target: document.getElementById("app") as HTMLElement
});
