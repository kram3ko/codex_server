// Single-user JWT auth with proactive refresh.
//
// Flow:
//   login()  → server returns JWT, store it, schedule a refresh just before exp.
//   refresh() → server reissues JWT using current Bearer. On failure → logout.
//   logout()  → drop token, cancel timer, emit "auth:logout" so the UI re-renders.
import { createClient } from "@connectrpc/connect";

import { AuthService } from "../../gen/codex/v1/auth_pb";
import { clearToken, getToken, setToken } from "../../shared/lib/token";
import { transport } from "../../shared/lib/transport";

const REFRESH_LEAD_SECONDS = 60;
const LOGOUT_EVENT = "auth:logout";

function jwtExpMs(token: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

function isLive(token: string | null): token is string {
  if (!token) return false;
  const expMs = jwtExpMs(token);
  return expMs === null || expMs > Date.now();
}

let token: string | null = getToken();
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
const client = createClient(AuthService, transport);

function cancelTimer(): void {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

function scheduleRefresh(): void {
  cancelTimer();
  if (!token) return;
  const expMs = jwtExpMs(token);
  if (expMs === null) return;
  const delay = Math.max(0, expMs - Date.now() - REFRESH_LEAD_SECONDS * 1000);
  refreshTimer = setTimeout(() => void auth.refresh(), delay);
}

function apply(newToken: string): void {
  token = newToken;
  setToken(newToken);
  scheduleRefresh();
}

export const auth = {
  get token(): string | null {
    return token;
  },

  get signedIn(): boolean {
    return isLive(token);
  },

  async login(email: string, password: string): Promise<void> {
    const response = await client.login({ email, password });
    apply(response.accessToken);
  },

  async refresh(): Promise<boolean> {
    if (!token) return false;
    try {
      const response = await client.refresh({});
      apply(response.accessToken);
      return true;
    } catch {
      auth.logout();
      return false;
    }
  },

  logout(): void {
    const wasSignedIn = token !== null;
    token = null;
    clearToken();
    cancelTimer();
    if (wasSignedIn) {
      window.dispatchEvent(new CustomEvent(LOGOUT_EVENT));
    }
  }
};

// Bootstrap: clear stale token; schedule refresh if still valid.
if (token && !isLive(token)) {
  clearToken();
  token = null;
} else if (token) {
  scheduleRefresh();
}
