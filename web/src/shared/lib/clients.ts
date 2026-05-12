import { createClient } from "@connectrpc/connect";

import { ChatService } from "../../gen/codex/v1/chat_pb";
import { MessageService } from "../../gen/codex/v1/message_pb";
import { NotesService } from "../../gen/codex/v1/notes_pb";
import { UploadsService } from "../../gen/codex/v1/uploads_pb";
import { transport } from "./transport";

export const chatClient = createClient(ChatService, transport);
export const messageClient = createClient(MessageService, transport);
export const notesClient = createClient(NotesService, transport);
export const uploadsClient = createClient(UploadsService, transport);
