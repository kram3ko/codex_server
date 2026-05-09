import { createPromiseClient } from "@connectrpc/connect";

import { AuthService } from "../../gen/codex/v1/auth_connect";
import { clearToken, getToken, setToken } from "../../shared/lib/token";
import { transport } from "../../shared/lib/transport";

export const auth = {
  token: getToken(),

  get signedIn(): boolean {
    return Boolean(this.token);
  },

  async login(apiToken: string): Promise<void> {
    const client = createPromiseClient(AuthService, transport);
    const response = await client.login({ token: apiToken });
    this.token = response.accessToken;
    setToken(response.accessToken);
  },

  logout(): void {
    this.token = null;
    clearToken();
  }
};
