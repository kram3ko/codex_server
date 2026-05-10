import { createPromiseClient } from "@connectrpc/connect";

import { ChatService } from "../../gen/codex/v1/chat_connect";
import { MessageService } from "../../gen/codex/v1/message_connect";
import { NotesService } from "../../gen/codex/v1/notes_connect";
import { UploadsService } from "../../gen/codex/v1/uploads_connect";
import { transport } from "./transport";

export const chatClient = createPromiseClient(ChatService, transport);
export const messageClient = createPromiseClient(MessageService, transport);
export const notesClient = createPromiseClient(NotesService, transport);
export const uploadsClient = createPromiseClient(UploadsService, transport);
