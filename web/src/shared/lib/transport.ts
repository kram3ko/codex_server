// Connect-RPC transport — JSON over HTTP/2 to /api, plus auth interceptor
// that injects `Authorization: Bearer <jwt>` from token storage.
import type { Interceptor } from "@connectrpc/connect";
import { createConnectTransport } from "@connectrpc/connect-web";

import { getToken } from "./token";

const authInterceptor: Interceptor = (next) => async (req) => {
  const token = getToken();
  if (token) {
    req.header.set("Authorization", `Bearer ${token}`);
  }
  return next(req);
};

export const transport = createConnectTransport({
  baseUrl: "/api",
  useBinaryFormat: false,
  interceptors: [authInterceptor]
});
