// Connect-RPC transport — JSON over HTTP/2 до /api.
//
// JWT живе у HttpOnly cookie `codex_jwt` — браузер додає його автоматично
// (через `credentials: 'include'`), а JavaScript його не бачить (CLAUDE.md §5).
// Interceptor-Bearer більше не потрібен.
//
// Refresh-retry: на 401 викликаємо `refreshFn` (зареєстрованим у `auth.ts`),
// яка робить `AuthService.Refresh` — сервер видає свіжий cookie, і retry
// проходить уже з оновленою сесією. `registerRefresh` уникає circular import.
import { Code, ConnectError, type Interceptor } from "@connectrpc/connect";
import { createConnectTransport } from "@connectrpc/connect-web";

type RefreshFn = () => Promise<boolean>;

let refreshFn: RefreshFn | null = null;

export function registerRefresh(fn: RefreshFn): void {
  refreshFn = fn;
}

const refreshRetryInterceptor: Interceptor = (next) => async (req) => {
  try {
    return await next(req);
  } catch (err) {
    if (
      !(err instanceof ConnectError) ||
      err.code !== Code.Unauthenticated ||
      refreshFn === null
    ) {
      throw err;
    }
    const refreshed = await refreshFn();
    if (!refreshed) throw err;
    return next(req);
  }
};

export const transport = createConnectTransport({
  baseUrl: "/api",
  useBinaryFormat: false,
  interceptors: [refreshRetryInterceptor],
  // Браузер шле HttpOnly cookie автоматично за умови credentials:'include'.
  fetch: (input, init) => fetch(input, { ...init, credentials: "include" })
});
