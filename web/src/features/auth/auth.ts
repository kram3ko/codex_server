// JWT доставляється у HttpOnly cookie (`codex_jwt`) — JavaScript його не
// бачить (CLAUDE.md §5). Тут тримаємо лише похідний `signedIn`-стан і
// expiry-таймер для proactive Refresh.
//
// Flow:
//   login()/register() → server set'ить cookie → ми лишень знімаємо expiry
//     з LoginResponse.expires_in і шедулимо refresh.
//   refresh()  → виклик RPC; на success — сервер видає свіжий cookie + ми
//     перепланимо наступний refresh.
//   logout()   → AuthService.Logout RPC очищує cookie + знімаємо локальний
//     `signedIn` сигнал.
import { createClient } from "@connectrpc/connect";

import { AuthService } from "../../gen/codex/v1/auth_pb";
import { registerRefresh, transport } from "../../shared/lib/transport";

const REFRESH_LEAD_SECONDS = 60;
const LOGOUT_EVENT = "auth:logout";
const LOGIN_EVENT = "auth:login";

let signedIn = false;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let refreshInFlight: Promise<boolean> | null = null;
const client = createClient(AuthService, transport);

function cancelTimer(): void {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

function scheduleRefresh(expiresInSeconds: number): void {
  cancelTimer();
  const delay = Math.max(0, (expiresInSeconds - REFRESH_LEAD_SECONDS) * 1000);
  refreshTimer = setTimeout(() => void auth.refresh(), delay);
}

export const auth = {
  get signedIn(): boolean {
    return signedIn;
  },

  async login(email: string, password: string): Promise<void> {
    const response = await client.login({ email, password });
    signedIn = true;
    scheduleRefresh(Number(response.expiresIn));
    window.dispatchEvent(new CustomEvent(LOGIN_EVENT));
  },

  async register(
    email: string,
    password: string,
    displayName: string,
    inviteToken: string,
  ): Promise<void> {
    const response = await client.register({
      email,
      password,
      displayName,
      inviteToken,
    });
    signedIn = true;
    scheduleRefresh(Number(response.expiresIn));
    window.dispatchEvent(new CustomEvent(LOGIN_EVENT));
  },

  // Single-flight: одночасні 401 від N RPC викликають refresh один раз.
  async refresh(): Promise<boolean> {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = (async () => {
      try {
        const response = await client.refresh({});
        const wasSignedIn = signedIn;
        signedIn = true;
        scheduleRefresh(Number(response.expiresIn));
        if (!wasSignedIn) {
          window.dispatchEvent(new CustomEvent(LOGIN_EVENT));
        }
        return true;
      } catch {
        signedIn = false;
        cancelTimer();
        return false;
      }
    })();
    try {
      return await refreshInFlight;
    } finally {
      refreshInFlight = null;
    }
  },

  async logout(): Promise<void> {
    const wasSignedIn = signedIn;
    signedIn = false;
    cancelTimer();
    // Сервер скидає cookie через Logout RPC. Якщо мережа впала — клієнт уже
    // вважає себе вилогіненим, на наступний RPC отримає 401 і викине у Login.
    try {
      await client.logout({});
    } catch {
      // Best-effort — клієнт-стан уже скинутий.
    }
    if (wasSignedIn) {
      window.dispatchEvent(new CustomEvent(LOGOUT_EVENT));
    }
  }
};

// Bootstrap: пробуємо refresh — якщо cookie існує і валідний, отримаємо
// свіжий + signedIn=true. Якщо ні (немає cookie / прострочений) — тихо
// лишаємось signedIn=false і user бачить login-форму.
void auth.refresh().catch(() => {
  /* no-op — bootstrap-only, will fall through to login screen */
});

registerRefresh(() => auth.refresh());
