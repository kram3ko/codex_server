// Connect-RPC transport — JSON over HTTP/2 to /api з двома interceptor'ами:
// (1) Bearer injection з token-storage, (2) refresh-retry на UNAUTHENTICATED.
//
// Refresh-retry уникає circular import (auth.ts → transport.ts) через
// `registerRefresh()` — auth-модуль реєструє callback на boot, до того
// interceptor просто пропускає 401 далі.
import { Code, ConnectError, type Interceptor } from "@connectrpc/connect";
import { createConnectTransport } from "@connectrpc/connect-web";

import { getToken } from "./token";

type RefreshFn = () => Promise<boolean>;

let refreshFn: RefreshFn | null = null;

export function registerRefresh(fn: RefreshFn): void {
  refreshFn = fn;
}

const authInterceptor: Interceptor = (next) => async (req) => {
  const token = getToken();
  if (token) {
    req.header.set("Authorization", `Bearer ${token}`);
  }
  return next(req);
};

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
    // На retry authInterceptor сам підставить свіжий token з getToken().
    return next(req);
  }
};

export const transport = createConnectTransport({
  baseUrl: "/api",
  useBinaryFormat: false,
  // Order matters: refreshRetry зовнішній → ловить 401 від внутрішніх;
  // на retry знов проходить через authInterceptor (свіжий Bearer).
  interceptors: [refreshRetryInterceptor, authInterceptor]
});
