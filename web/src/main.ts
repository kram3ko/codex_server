import "./styles/index.css";

import * as Sentry from "@sentry/svelte";
import { mount } from "svelte";

import App from "./App.svelte";

if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    release: import.meta.env.VITE_SENTRY_RELEASE || undefined,
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? 0),
    sendDefaultPii: false
  });
}

mount(App, {
  target: document.getElementById("app") as HTMLElement
});
