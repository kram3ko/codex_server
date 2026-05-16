<script lang="ts">
  import { AlertTriangle } from "lucide-svelte";
  import { onDestroy, onMount, tick } from "svelte";

  import ChatList from "./ChatList.svelte";
  import Composer from "./Composer.svelte";
  import MessageList from "./MessageList.svelte";
  import { createTypewriter } from "./typewriter.svelte";
  import type { ToolEvent } from "./ToolCall.svelte";
  import { turnSignal } from "./turnSignal.svelte";
  import { create } from "@bufbuild/protobuf";
  import { TimestampSchema } from "@bufbuild/protobuf/wkt";

  import type { Attachment as ChatAttachment, Chat } from "../../gen/codex/v1/chat_pb";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import { MessageSchema } from "../../gen/codex/v1/message_pb";

  function nowTimestamp() {
    const ms = Date.now();
    return create(TimestampSchema, {
      seconds: BigInt(Math.floor(ms / 1000)),
      nanos: (ms % 1000) * 1_000_000
    });
  }
  import Spinner from "../../shared/components/Spinner.svelte";
  import { chatClient, messageClient } from "../../shared/lib/clients";

  let chats = $state<Chat[]>([]);
  let selected = $state<Chat | null>(null);
  let messages = $state<ChatMessage[]>([]);
  let streamingClientId = $state<string | null>(null);
  let tools = $state<ToolEvent[]>([]);
  let attachments = $state<ChatAttachment[]>([]);
  let loading = $state(false);
  let busy = $state(false);
  let error = $state("");
  let info = $state("");
  let draftStartedAt = $state<number | undefined>(undefined);
  let lastActivityAt = $state<number | undefined>(undefined);
  let activeTurnId = $state(0);
  let streamedPrefix = "";

  const IDLE_TIMEOUT_MS = 300_000;

  let infoTimer: ReturnType<typeof setTimeout> | null = null;
  function flashInfo(message: string): void {
    info = message;
    if (infoTimer) clearTimeout(infoTimer);
    infoTimer = setTimeout(() => { info = ""; infoTimer = null; }, 3000);
  }

  onDestroy(() => {
    if (infoTimer) clearTimeout(infoTimer);
  });

  const typer = createTypewriter();

  function clientIdOf(message: ChatMessage): string | undefined {
    const meta = message.meta as Record<string, unknown> | undefined;
    const cid = meta?.client_id;
    return typeof cid === "string" ? cid : undefined;
  }

  const displayMessages = $derived.by((): ChatMessage[] => {
    if (!streamingClientId) return messages;
    return messages.map((m) =>
      clientIdOf(m) === streamingClientId
        ? create(MessageSchema, { ...m, text: typer.displayed })
        : m
    );
  });

  async function loadChats(silent = false) {
    if (!silent) loading = true;
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

  const PAGE_SIZE = 10;
  let loadingOlder = $state(false);
  let hasMoreOlder = $state(false);

  async function loadChatMessages(chat: Chat) {
    selected = chat;
    streamingClientId = null;
    const response = await messageClient.listMessages({
      chatId: chat.id,
      pagination: { limit: PAGE_SIZE }
    });
    messages = [...response.messages];
    hasMoreOlder = response.messages.length >= PAGE_SIZE;
  }

  async function loadOlderMessages() {
    if (loadingOlder || !hasMoreOlder || !selected || messages.length === 0) return;
    const oldest = messages[0];
    if (!oldest) return;
    loadingOlder = true;
    try {
      const response = await messageClient.listMessages({
        chatId: selected.id,
        pagination: { limit: PAGE_SIZE },
        beforeId: oldest.id
      });
      messages = [...response.messages, ...messages];
      hasMoreOlder = response.messages.length >= PAGE_SIZE;
    } finally {
      loadingOlder = false;
    }
  }

  async function selectChat(chat: Chat) {
    // User explicitly switched chat — drop turn-local state.
    const previous = selected;
    const wasBusy = busy;
    activeTurnId += 1;
    busy = false;
    streamingClientId = null;
    draftStartedAt = undefined;
    lastActivityAt = undefined;
    typer.reset();
    tools = [];
    attachments = [];
    streamedPrefix = "";
    if (wasBusy && previous) {
      await chatClient.interruptTurn({ chatId: previous.id }).catch(() => undefined);
    }
    await loadChatMessages(chat);
  }

  async function send(text: string, imageIds: bigint[] = [], audioIds: bigint[] = []) {
    const uploadIds = [...imageIds, ...audioIds];
    // Busy + no uploads → пробуємо steer running turn. Reject = turn закінчився
    // між нашим busy=true і RPC; просто відкриваємо новий turn без interrupt.
    // Uploads + busy → юзер свідомо хоче новий turn (steer API не приймає
    // attachments), тож interrupt'имо явно.
    if (busy && selected && uploadIds.length === 0) {
      const resp = await chatClient
        .steerTurn({ chatId: selected.id, text })
        .catch(() => null);
      if (resp?.accepted) {
        const userMessage = create(MessageSchema, {
          id: BigInt(Date.now()),
          chatId: selected.id,
          role: 1,
          text,
          meta: { steered: true },
          createdAt: nowTimestamp()
        });
        // Split already-visible assistant text before the steered user message.
        // The same Codex turn keeps streaming after steer, but visually it is a
        // new assistant segment responding to the additional user context.
        const partialText = typer.displayed;
        const partialAssistant = partialText.trim()
          ? create(MessageSchema, {
              id: -BigInt(Date.now() + 1),
              chatId: selected.id,
              role: 2,
              text: partialText,
              meta: { partial: true },
              createdAt: nowTimestamp()
            })
          : null;
        if (partialAssistant) {
          streamedPrefix += partialText;
          typer.reset();
        }
        lastActivityAt = Date.now();
        const streamIdx = streamingClientId
          ? messages.findIndex((m) => clientIdOf(m) === streamingClientId)
          : -1;
        const streaming = streamIdx >= 0 ? messages[streamIdx] : undefined;
        if (streaming) {
          const before = messages.slice(0, streamIdx);
          const after = messages.slice(streamIdx + 1);
          messages = partialAssistant
            ? [...before, partialAssistant, userMessage, streaming, ...after]
            : [...before, userMessage, streaming, ...after];
        } else {
          messages = [...messages, userMessage];
        }
        return;
      }
      flashInfo("Turn finished — message sent as new turn");
    } else if (busy && selected) {
      await chatClient.interruptTurn({ chatId: selected.id }).catch(() => undefined);
    }
    const turnId = activeTurnId + 1;
    activeTurnId = turnId;
    busy = true;
    error = "";
    tools = [];
    attachments = [];
    streamedPrefix = "";
    typer.reset();
    draftStartedAt = Date.now();
    lastActivityAt = Date.now();
    const userMetaJson: { upload_ids?: number[]; audio_upload_ids?: number[] } = {};
    if (imageIds.length) userMetaJson.upload_ids = imageIds.map((id) => Number(id));
    if (audioIds.length) userMetaJson.audio_upload_ids = audioIds.map((id) => Number(id));
    const userMessage = create(MessageSchema, {
      id: BigInt(Date.now()),
      chatId: selected?.id ?? 0n,
      role: 1,
      text,
      meta: Object.keys(userMetaJson).length ? userMetaJson : undefined,
      createdAt: nowTimestamp()
    });
    const clientId = crypto.randomUUID();
    streamingClientId = clientId;
    const streamingPlaceholder = create(MessageSchema, {
      id: -BigInt(Date.now()),
      chatId: selected?.id ?? 0n,
      role: 2,
      text: "",
      meta: { client_id: clientId },
      createdAt: nowTimestamp()
    });
    messages = [...messages, userMessage, streamingPlaceholder];

    try {
      const stream = chatClient.runTurn({
        chatId: selected?.id,
        text,
        uploadIds,
        clientId
      });
      for await (const event of stream) {
        if (turnId !== activeTurnId) {
          break;
        }
        lastActivityAt = Date.now();
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
                args: event.kind.value.args,
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
            if (done.steeredFallback) {
              // Текст пішов у running turn — наш placeholder зайвий, реальна відповідь прийде там.
              messages = messages.filter((m) => clientIdOf(m) !== clientId);
              streamingClientId = null;
              draftStartedAt = undefined;
              lastActivityAt = undefined;
              typer.reset();
              streamedPrefix = "";
              tools = [];
              attachments = [];
              flashInfo("Message added to running turn");
              void loadChats(true);
              break;
            }
            if (done.finalText) {
              const finalText = streamedPrefix && done.finalText.startsWith(streamedPrefix)
                ? done.finalText.slice(streamedPrefix.length)
                : done.finalText;
              const already = typer.displayed;
              if (finalText.length > already.length && finalText.startsWith(already)) {
                typer.push(finalText.slice(already.length));
              }
            }
            await typer.drained();
            const persisted = done.message;
            if (persisted) {
              // ID swap: streaming placeholder → real DB message by client_id.
              // Same key (client_id) keeps DOM instance stable, no remount.
              const renderedPersisted = streamedPrefix
                ? create(MessageSchema, { ...persisted, text: typer.displayed })
                : persisted;
              messages = messages.map((m) =>
                clientIdOf(m) === clientId ? renderedPersisted : m
              );
            } else {
              messages = messages.map((m) =>
                clientIdOf(m) === clientId
                  ? create(MessageSchema, { ...m, text: typer.displayed })
                  : m
              );
            }
            streamingClientId = null;
            draftStartedAt = undefined;
            lastActivityAt = undefined;
            typer.reset();
            streamedPrefix = "";
            tools = [];
            attachments = [];
            void loadChats(true);
            turnSignal.doneCount += 1;
            break;
          }
          case "error":
            if (turnId !== activeTurnId) {
              break;
            }
            {
              const code = event.kind.value.code;
              const detail = event.kind.value.detail;
              if (code === "turn_timeout" && detail === "stale-turn-notifications") {
                error = "Codex завис — thread скинуто, історію (20 останніх повідомлень) буде відновлено на наступному turn'і. Повтори запит.";
              } else if (code === "turn_busy") {
                error = "Інший turn у цьому чаті вже активний — почекай завершення.";
              } else {
                error = detail || code;
              }
            }
            // Drop streaming placeholder on error.
            messages = messages.filter((m) => clientIdOf(m) !== clientId);
            streamingClientId = null;
            streamedPrefix = "";
            lastActivityAt = undefined;
            draftStartedAt = undefined;
            break;
        }
        await tick();
      }
    } catch (exc) {
      if (turnId === activeTurnId) {
        error = exc instanceof Error ? exc.message : "Turn failed";
        messages = messages.filter((m) => clientIdOf(m) !== clientId);
        streamingClientId = null;
        streamedPrefix = "";
        lastActivityAt = undefined;
        draftStartedAt = undefined;
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
    // Snapshot partial streamed text into committed message so user sees the
    // partial reply instead of empty hole. Same client_id key keeps DOM stable.
    const snapshot = typer.displayed;
    if (streamingClientId && snapshot) {
      const cid = streamingClientId;
      messages = messages.map((m) =>
        clientIdOf(m) === cid ? create(MessageSchema, { ...m, text: snapshot }) : m
      );
    } else if (streamingClientId) {
      const cid = streamingClientId;
      messages = messages.filter((m) => clientIdOf(m) !== cid);
    }
    streamingClientId = null;
    typer.reset();
    streamedPrefix = "";
    draftStartedAt = undefined;
    lastActivityAt = undefined;
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
    {#if info}
      <div class="border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-1.5 text-xs text-[var(--color-text-muted)]">
        {info}
      </div>
    {/if}

    {#if loading && !selected}
      <div class="grid flex-1 place-items-center">
        <Spinner />
      </div>
    {:else}
      <MessageList messages={displayMessages} streamingClientId={streamingClientId} {tools} {attachments} {draftStartedAt} {lastActivityAt} idleTimeoutMs={IDLE_TIMEOUT_MS} {loadingOlder} {hasMoreOlder} onloadolder={loadOlderMessages} />
      <Composer {busy} onsend={send} oninterrupt={interrupt} />
    {/if}
  </section>
</main>
