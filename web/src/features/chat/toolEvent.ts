import type { ToolError } from "../../gen/codex/v1/chat_pb";

export type ToolEvent = {
  id: string;
  name: string;
  args?: unknown;
  text?: string;
  error?: ToolError;
  status: "running" | "done" | "error";
};
