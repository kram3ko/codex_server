export type IntegrationKind = "github" | "gitlab" | "ssh" | "telegram";
export interface Integration {
  id: number;
  name: string;
  kind: IntegrationKind;
  host: string;
  username: string;
  enabled: boolean;
  has_secret: boolean;
  fingerprint: string | null;
  bot_username: string | null;
  created_at: string;
  updated_at: string;
  telegram: { admin_ids: number[]; webhook_url: string };
}
export interface TelegramChat {
  id: number;
  bot_id: string;
  chat_id: string;
  title: string;
  kind: string;
  membership: string;
  replies_enabled: boolean;
  updated_at: string;
}
export async function settingsApi<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/settings${path}`, {
    method, credentials: "include",
    headers: { "Content-Type": "application/json", "X-Settings-Request": "1" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export interface UserProfile {
  id: string;
  tg_username: string | null;
  last_active_at: string | null;
}
export interface TurnLimits {
  active: number;
  hourly: number;
}
export interface RuntimeLimits {
  web_user: TurnLimits;
  tg_guest: TurnLimits;
  tg_guest_max_upload_mb: number;
}
