import type { Attachment as ChatAttachment } from "../../gen/codex/v1/chat_pb";
import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
import type { ToolEvent } from "./toolEvent";

export type LiveDraft = {
  clientId: string;
  messages: ChatMessage[];
  text: string;
  streamedPrefix: string;
  lastEventId: string;
  serverTurnId: bigint | null;
  tools: ToolEvent[];
  attachments: ChatAttachment[];
  draftStartedAt: number | undefined;
  lastActivityAt: number | undefined;
};

export type ScrollTarget = "bottom" | "stream";

export type ScrollIntent = {
  seq: number;
  target: ScrollTarget;
};

export function clientIdOf(message: ChatMessage): string | undefined {
  const meta = message.meta as Record<string, unknown> | undefined;
  const cid = meta?.client_id;
  return typeof cid === "string" ? cid : undefined;
}

export function isEmptyAssistantMessage(message: ChatMessage): boolean {
  if (message.role !== 2 || message.text.trim()) return false;
  const meta = message.meta as Record<string, unknown> | undefined;
  return !meta?.partial && !meta?.calls && !meta?.upload_ids;
}

export function suffixAfterPrefix(prefix: string, source: string, current: string): string {
  if (!prefix) return source || current;
  if (!source) return current;
  if (source.startsWith(prefix)) return source.slice(prefix.length) || current;
  return current || source;
}
