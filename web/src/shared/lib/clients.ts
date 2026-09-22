import { createClient } from "@connectrpc/connect";

import { AdminService } from "../../gen/codex/v1/admin_pb";
import { ChatService } from "../../gen/codex/v1/chat_pb";
import { CodexService } from "../../gen/codex/v1/codex_pb";
import { MessageService } from "../../gen/codex/v1/message_pb";
import { NotesService } from "../../gen/codex/v1/notes_pb";
import { UploadsService } from "../../gen/codex/v1/uploads_pb";
import { UserService } from "../../gen/codex/v1/user_pb";
import { transport } from "./transport";

export const adminClient = createClient(AdminService, transport);
export const chatClient = createClient(ChatService, transport);
export const codexClient = createClient(CodexService, transport);
export const messageClient = createClient(MessageService, transport);
export const notesClient = createClient(NotesService, transport);
export const uploadsClient = createClient(UploadsService, transport);
export const userClient = createClient(UserService, transport);
