<script lang="ts">
  import { AlertTriangle } from "lucide-svelte";
  import { onMount, tick } from "svelte";

  import ChatList from "./ChatList.svelte";
  import Composer from "./Composer.svelte";
  import MessageList from "./MessageList.svelte";
  import { createTypewriter } from "./typewriter.svelte";
  import type { ToolEvent } from "./ToolCall.svelte";
  import { Struct } from "@bufbuild/protobuf";

  import type { Attachment as ChatAttachment, Chat } from "../../gen/codex/v1/chat_pb";
  import { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import Spinner from "../../shared/components/Spinner.svelte";
  import { chatClient, messageClient } from "../../shared/lib/clients";

  let chats = $state<Chat[]>([]);
  let selected = $state<Chat | null>(null);
  let messages = $state<ChatMessage[]>([]);
  let draft = $state<ChatMessage | null>(null);
  let tools = $state<ToolEvent[]>([]);
  let attachments = $state<ChatAttachment[]>([]);
  let loading = $state(false);
  let busy = $state(false);
  let error = $state("");
  let draftStartedAt = $state<number | undefined>(undefined);
  let activeTurnId = $state(0);

  const typer = createTypewriter();
  const liveDraft = $derived(draft ? new ChatMessage({ ...draft, text: typer.displayed }) : null);

  async function loadChats() {
    loading = true;
    error = "";
    try {
      const response = await chatClient.listChats({ pagination: { limit: 100 } });
      chats = [...response.chats];
      if (!selected && chats[0]) {
        await selectChat(chats[0]);
      }
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to load chats";
    } finally {
      loading = false;
    }
  }

  async function loadChatMessages(chat: Chat) {
    selected = chat;
    draft = null;
    const response = await messageClient.listMessages({
      chatId: chat.id,
      pagination: { limit: 250 }
    });
    messages = [...response.messages];
  }

  async function selectChat(chat: Chat) {
    // User explicitly switched chat — drop turn-local state.
    const previous = selected;
    const wasBusy = busy;
    activeTurnId += 1;
    busy = false;
    draft = null;
    draftStartedAt = undefined;
    typer.reset();
    tools = [];
    attachments = [];
    if (wasBusy && previous) {
      await chatClient.interruptTurn({ chatId: previous.id }).catch(() => undefined);
    }
    await loadChatMessages(chat);
  }

  async function send(text: string, uploadIds: bigint[] = []) {
    // Steer the running turn instead of interrupting+restarting. Codex
    // appends `text` to the in-flight prompt; uploads still require a fresh
    // turn (sidecar's steer API only accepts text), so fall through if any.
    if (busy && selected && uploadIds.length === 0) {
      const resp = await chatClient
        .steerTurn({ chatId: selected.id, text })
        .catch(() => null);
      if (resp?.accepted) {
        const userMessage = new ChatMessage({
          id: BigInt(Date.now()),
          chatId: selected.id,
          role: 1,
          text
        });
        messages = [...messages, userMessage];
        return;
      }
    }
    if (busy && selected) {
      await chatClient.interruptTurn({ chatId: selected.id }).catch(() => undefined);
    }
    const turnId = activeTurnId + 1;
    activeTurnId = turnId;
    busy = true;
    error = "";
    tools = [];
    attachments = [];
    typer.reset();
    draftStartedAt = Date.now();
    const userMessage = new ChatMessage({
      id: BigInt(Date.now()),
      chatId: selected?.id ?? 0n,
      role: 1,
      text,
      meta: uploadIds.length
        ? Struct.fromJson({ upload_ids: uploadIds.map((id) => Number(id)) })
        : undefined
    });
    messages = [...messages, userMessage];
    draft = new ChatMessage({
      id: BigInt(Date.now() + 1),
      chatId: selected?.id ?? 0n,
      role: 2,
      text: ""
    });

    try {
      const stream = chatClient.runTurn({
        chatId: selected?.id,
        text,
        uploadIds
      });
      for await (const event of stream) {
        if (turnId !== activeTurnId) {
          break;
        }
        switch (event.kind.case) {
          case "token":
            typer.push(event.kind.value.delta);
            break;
          case "toolCall":
            tools = [
              ...tools,
              {
                id: `${Date.now()}:${tools.length}`,
                name: event.kind.value.name,
                args: event.kind.value.args?.toJson(),
                status: "running"
              }
            ];
            break;
          case "toolResult": {
            const result = event.kind.value;
            const idx = tools.findIndex((tool) => tool.name === result.name && tool.status === "running");
            if (idx >= 0) {
              tools = tools.map((tool, i) =>
                i === idx
                  ? {
                      ...tool,
                      text: result.text,
                      error: result.error,
                      status: result.error ? "error" : "done"
                    }
                  : tool
              );
            } else {
              tools = [
                ...tools,
                {
                  id: `${Date.now()}:${tools.length}`,
                  name: result.name,
                  text: result.text,
                  error: result.error,
                  status: result.error ? "error" : "done"
                }
              ];
            }
            attachments = [...attachments, ...result.attachments];
            break;
          }
          case "done": {
            const done = event.kind.value;
            if (turnId !== activeTurnId) {
              break;
            }
            if (done.finalText) {
              const already = typer.displayed;
              if (done.finalText.length > already.length && done.finalText.startsWith(already)) {
                typer.push(done.finalText.slice(already.length));
              }
            }
            await typer.drained();
            if (draft) {
              messages = [...messages, new ChatMessage({ ...draft, text: typer.displayed })];
            }
            draft = null;
            draftStartedAt = undefined;
            typer.reset();
            await loadChats();
            if (done.chatId) {
              const current = chats.find((chat) => chat.id === done.chatId);
              // Auto-refresh after server persists turn — drop transient
              // tool/attachment state; historical upload_ids own rendering.
              if (current) {
                await loadChatMessages(current);
                tools = [];
                attachments = [];
              }
            }
            break;
          }
          case "error":
            if (turnId !== activeTurnId) {
              break;
            }
            error = event.kind.value.detail || event.kind.value.code;
            draft = null;
            break;
        }
        await tick();
      }
    } catch (exc) {
      if (turnId === activeTurnId) {
        error = exc instanceof Error ? exc.message : "Turn failed";
        draft = null;
      }
    } finally {
      if (turnId === activeTurnId) {
        busy = false;
      }
    }
  }

  async function interrupt() {
    if (!selected) {
      return;
    }
    activeTurnId += 1;
    // Snapshot whatever the model already streamed so the user sees the
    // partial reply instead of an empty hole. Server-side persistence is
    // a separate concern (TURN_INTERRUPTED is logged, partial text isn't
    // saved yet).
    if (draft && typer.displayed) {
      messages = [...messages, new ChatMessage({ ...draft, text: typer.displayed })];
    }
    typer.reset();
    draft = null;
    draftStartedAt = undefined;
    await chatClient.interruptTurn({ chatId: selected.id });
    busy = false;
  }

  onMount(() => {
    void loadChats();
  });
</script>

<main class="flex h-[calc(100vh-3.5rem)] min-h-0">
  <ChatList chats={chats} selectedId={selected?.id ?? null} loading={loading} onrefresh={loadChats} onselect={selectChat} />

  <section class="flex min-w-0 flex-1 flex-col">
    {#if error}
      <div class="flex items-center gap-2 border-b border-[#e7c9c1] bg-[#fff5f2] px-4 py-2 text-sm text-[#a33a2b]">
        <AlertTriangle size={16} />
        {error}
      </div>
    {/if}

    {#if loading && !selected}
      <div class="grid flex-1 place-items-center">
        <Spinner />
      </div>
    {:else}
      <MessageList {messages} draft={liveDraft} {tools} {attachments} {draftStartedAt} />
      <Composer {busy} onsend={send} oninterrupt={interrupt} />
    {/if}
  </section>
</main>
