<script lang="ts">
  import { AlertTriangle } from "lucide-svelte";
  import { onDestroy, onMount, tick } from "svelte";
  import { SvelteSet } from "svelte/reactivity";

  import ChatList from "./ChatList.svelte";
  import Composer from "./Composer.svelte";
  import MessageList from "./MessageList.svelte";
  import { createTypewriter } from "./typewriter.svelte";
  import type { ToolEvent } from "./ToolCall.svelte";
  import { turnSignal } from "./turnSignal.svelte";
  import { create } from "@bufbuild/protobuf";
  import { TimestampSchema } from "@bufbuild/protobuf/wkt";

  import type { Attachment as ChatAttachment, Chat, ChatEvent } from "../../gen/codex/v1/chat_pb";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import { MessageSchema } from "../../gen/codex/v1/message_pb";
  import Spinner from "../../shared/components/Spinner.svelte";
  import { chatClient, messageClient } from "../../shared/lib/clients";

  function nowTimestamp() {
    const ms = Date.now();
    return create(TimestampSchema, {
      seconds: BigInt(Math.floor(ms / 1000)),
      nanos: (ms % 1000) * 1_000_000
    });
  }

  let chats = $state<Chat[]>([]);
  let selected = $state<Chat | null>(null);
  let messages = $state<ChatMessage[]>([]);
  let streamingClientId = $state<string | null>(null);
  let tools = $state<ToolEvent[]>([]);
  let attachments = $state<ChatAttachment[]>([]);
  let loading = $state(false);
  let busy = $state(false);
  /** Chat IDs with a server-side STARTING/RUNNING turn (sidebar spinner source). */
  const busyChats = new SvelteSet<bigint>();

  /** Rebuild busyChats from the authoritative ListChats snapshot. */
  function syncBusyFromServer(list: Chat[]) {
    busyChats.clear();
    for (const c of list) if (c.activeTurnId) busyChats.add(c.id);
  }
  let error = $state("");
  let info = $state("");
  let draftStartedAt = $state<number | undefined>(undefined);
  let lastActivityAt = $state<number | undefined>(undefined);
  let activeTurnId = $state(0);
  let streamedPrefix = "";
  // Negative monotonic id для placeholder-рядків (resume/send). DB ids
  // позитивні autoincrement — від'ємні гарантовано не колізують.
  let placeholderSeq = -1n;

  const IDLE_TIMEOUT_MS = 300_000;

  let infoTimer: ReturnType<typeof setTimeout> | null = null;
  function flashInfo(message: string): void {
    info = message;
    if (infoTimer) clearTimeout(infoTimer);
    infoTimer = setTimeout(() => { info = ""; infoTimer = null; }, 3000);
  }

  onDestroy(() => {
    if (infoTimer) clearTimeout(infoTimer);
    resumeAbort?.abort();
    activityAbort?.abort();
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
      syncBusyFromServer(chats);
      if (!selected && chats[0]) {
        await selectChat(chats[0]);
      }
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to load chats";
    } finally {
      loading = false;
    }
  }

  async function createChat(title: string) {
    error = "";
    try {
      const chat = await chatClient.createChat({ title });
      chats = [chat, ...chats];
      await selectChat(chat);
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to create chat";
    }
  }

  async function renameChat(chat: Chat, title: string) {
    error = "";
    try {
      const updated = await chatClient.renameChat({ chatId: chat.id, title });
      chats = chats.map((c) => (c.id === chat.id ? updated : c));
      if (selected?.id === chat.id) selected = updated;
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to rename chat";
    }
  }

  async function deleteChat(chat: Chat) {
    error = "";
    try {
      await chatClient.deleteChat({ chatId: chat.id });
      chats = chats.filter((c) => c.id !== chat.id);
      if (selected?.id === chat.id) {
        selected = null;
        messages = [];
        if (chats[0]) await selectChat(chats[0]);
      }
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to delete chat";
    }
  }

  const PAGE_SIZE = 10;
  let loadingOlder = $state(false);
  let hasMoreOlder = $state(false);

  let resumeAbort: AbortController | null = null;

  async function loadChatMessages(chat: Chat) {
    selected = chat;
    streamingClientId = null;
    const response = await messageClient.listMessages({
      chatId: chat.id,
      pagination: { limit: PAGE_SIZE }
    });
    messages = [...response.messages];
    hasMoreOlder = response.messages.length >= PAGE_SIZE;
    // Resume-on-mount: якщо для цього чату є RUNNING turn (наприклад
    // hard-reset під час стрімінгу), tail-имо його. На terminal frame
    // re-load повідомлень підхопить final. Сервер на нема-active turn
    // одразу повертає synthetic terminal — fire-and-forget безпечний.
    resumeAbort?.abort();
    resumeAbort = new AbortController();
    void resumeActiveTurn(chat, resumeAbort);
  }

  async function resumeActiveTurn(chat: Chat, ctrl: AbortController) {
    // Замало знати "є activeturn чи нема" — потрібний повний live replay
    // через placeholder + typewriter, щоб відновлений стрім виглядав як
    // оригінальний send(). На `done` swap-аємо placeholder на persisted
    // (client_id у persisted інший — від оригінального send-а — тому swap
    // по локальному `resume-`-id який ми присвоїли placeholder-ові).
    const clientId = `resume-${chat.id}-${Date.now()}`;
    let placeholderAdded = false;
    let toolsLocal: ToolEvent[] = [];
    let attachmentsLocal: ChatAttachment[] = [];

    function ensurePlaceholder() {
      if (placeholderAdded || ctrl.signal.aborted) return;
      placeholderAdded = true;
      const placeholder = create(MessageSchema, {
        id: placeholderSeq--,
        chatId: chat.id,
        role: 2,
        text: "",
        meta: { client_id: clientId },
        createdAt: nowTimestamp(),
      });
      messages = [...messages, placeholder];
      streamingClientId = clientId;
      typer.reset();
      draftStartedAt = Date.now();
      lastActivityAt = Date.now();
      busy = true;
      busyChats.add(chat.id);
    }

    try {
      for await (const event of chatClient.tailTurn(
        { chatId: chat.id, afterId: "0" },
        { signal: ctrl.signal }
      )) {
        if (selected?.id !== chat.id) return;
        lastActivityAt = Date.now();
        switch (event.kind.case) {
          case "token":
            ensurePlaceholder();
            typer.push(event.kind.value.delta);
            break;
          case "toolCall":
            ensurePlaceholder();
            toolsLocal = [
              ...toolsLocal,
              {
                id: `${Date.now()}:${toolsLocal.length}`,
                name: event.kind.value.name,
                args: event.kind.value.args,
                status: "running",
              },
            ];
            tools = toolsLocal;
            break;
          case "toolResult": {
            ensurePlaceholder();
            const result = event.kind.value;
            const idx = toolsLocal.findIndex(
              (t) => t.name === result.name && t.status === "running"
            );
            if (idx >= 0) {
              toolsLocal = toolsLocal.map((t, i) =>
                i === idx
                  ? {
                      ...t,
                      text: result.text,
                      error: result.error,
                      status: result.error ? "error" : "done",
                    }
                  : t
              );
            } else {
              toolsLocal = [
                ...toolsLocal,
                {
                  id: `${Date.now()}:${toolsLocal.length}`,
                  name: result.name,
                  text: result.text,
                  error: result.error,
                  status: result.error ? "error" : "done",
                },
              ];
            }
            tools = toolsLocal;
            attachmentsLocal = [...attachmentsLocal, ...result.attachments];
            attachments = attachmentsLocal;
            break;
          }
          case "done": {
            await typer.drained();
            const persisted = event.kind.value.message;
            // Server тепер вкладає persisted message у synthetic terminal
            // (`_terminal_from_status` у service.py). Один з трьох шляхів:
            //   placeholder + persisted → swap
            //   no placeholder + persisted → append (turn finalize-нувся
            //     між loadChatMessages і tail RPC)
            //   no persisted → no-op (FAILED/CANCELLED або pre-attach turn)
            if (persisted && placeholderAdded) {
              messages = messages.map((m) =>
                clientIdOf(m) === clientId ? persisted : m
              );
            } else if (persisted && !messages.some((m) => m.id === persisted.id)) {
              messages = [...messages, persisted];
            }
            busyChats.delete(chat.id);
            cleanupResumeState(clientId);
            return;
          }
          case "error":
            messages = messages.filter((m) => clientIdOf(m) !== clientId);
            busyChats.delete(chat.id);
            cleanupResumeState(clientId);
            return;
        }
        await tick();
      }
    } catch {
      messages = messages.filter((m) => clientIdOf(m) !== clientId);
      cleanupResumeState(clientId);
    }
  }

  function cleanupResumeState(clientId: string) {
    // Чистимо global state ТІЛЬКИ якщо він усе ще наш — інакше aborted
    // resume міг би перетерти живий send() що почався пізніше.
    if (streamingClientId !== clientId) return;
    streamingClientId = null;
    draftStartedAt = undefined;
    lastActivityAt = undefined;
    typer.reset();
    tools = [];
    attachments = [];
    busy = false;
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

  /** Switch chat without interrupting the previous stream — `tailTurn` resumes on return. */
  async function selectChat(chat: Chat) {
    activeTurnId += 1;
    busy = false;
    streamingClientId = null;
    draftStartedAt = undefined;
    lastActivityAt = undefined;
    typer.reset();
    tools = [];
    attachments = [];
    streamedPrefix = "";
    await loadChatMessages(chat);
  }

  async function send(text: string, imageIds: bigint[] = [], audioIds: bigint[] = []) {
    // Відмінити resume-stream що ще тримається з попереднього mount —
    // інакше його `ensurePlaceholder()`/cleanup міг би перетерти state
    // нового send-у (streamingClientId/typer/tools/busy).
    resumeAbort?.abort();
    resumeAbort = null;
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
              id: placeholderSeq--,
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
    const chatAtSend = selected;
    if (chatAtSend) busyChats.add(chatAtSend.id);
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
      id: placeholderSeq--,
      chatId: selected?.id ?? 0n,
      role: 2,
      text: "",
      meta: { client_id: clientId },
      createdAt: nowTimestamp()
    });
    messages = [...messages, userMessage, streamingPlaceholder];
    let lastEventId = "";
    let serverTurnId: bigint | null = null;

    async function processStream(stream: AsyncIterable<ChatEvent>): Promise<boolean> {
      let terminalSeen = false;
      for await (const event of stream) {
        if (turnId !== activeTurnId) {
          break;
        }
        if (event.eventId) lastEventId = event.eventId;
        lastActivityAt = Date.now();
        switch (event.kind.case) {
          case "turnStarted":
            serverTurnId = event.kind.value.turnId;
            break;
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
            terminalSeen = true;
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
            terminalSeen = true;
            break;
        }
        await tick();
      }
      return terminalSeen;
    }

    // Stream EOF без `done`/`error` — backend turn міг штатно завершитися у БД,
    // але terminal event до клієнта не дойшов (TTL Redis-стріму, transport
    // drop without RST, etc). Reload з БД + reset streaming state, інакше UI
    // зависає у "streaming" попри готовий assistant message.
    async function recoverSilentEof() {
      messages = messages.filter((m) => clientIdOf(m) !== clientId);
      streamingClientId = null;
      streamedPrefix = "";
      lastActivityAt = undefined;
      draftStartedAt = undefined;
      typer.reset();
      tools = [];
      attachments = [];
      if (selected) await loadChatMessages(selected);
    }

    try {
      const ok = await processStream(chatClient.runTurn({
        chatId: selected?.id,
        text,
        uploadIds,
        clientId
      }));
      if (!ok && turnId === activeTurnId && selected) {
        const tailOk = await processStream(chatClient.tailTurn({
          chatId: selected.id,
          afterId: lastEventId || "0",
          ...(serverTurnId !== null ? { turnId: serverTurnId } : {})
        }));
        if (!tailOk && turnId === activeTurnId) {
          await recoverSilentEof();
        }
      }
    } catch (exc) {
      // Mid-turn disconnect → reconnect by `serverTurnId` якщо ми його встигли
      // отримати; інакше fallback на chat-based lookup.
      if (turnId === activeTurnId && selected) {
        try {
          const tailOk = await processStream(chatClient.tailTurn({
            chatId: selected.id,
            afterId: lastEventId || "0",
            ...(serverTurnId !== null ? { turnId: serverTurnId } : {})
          }));
          if (!tailOk && turnId === activeTurnId) {
            await recoverSilentEof();
          }
        } catch (tailExc) {
          if (turnId === activeTurnId) {
            error = tailExc instanceof Error ? tailExc.message : "Stream lost";
            messages = messages.filter((m) => clientIdOf(m) !== clientId);
            streamingClientId = null;
            streamedPrefix = "";
            lastActivityAt = undefined;
            draftStartedAt = undefined;
          }
        }
      } else if (turnId === activeTurnId) {
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
        if (chatAtSend) busyChats.delete(chatAtSend.id);
      }
    }
  }

  async function interrupt() {
    if (!selected) {
      return;
    }
    resumeAbort?.abort();
    resumeAbort = null;
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
    busyChats.delete(selected.id);
  }

  let activityAbort: AbortController | null = null;
  const ACTIVITY_BACKOFF_INITIAL_MS = 1_000;
  const ACTIVITY_BACKOFF_MAX_MS = 30_000;

  /** Open the per-user activity stream; on drop, exp-backoff + snapshot resync. */
  async function subscribeChatActivity() {
    activityAbort?.abort();
    const ctrl = new AbortController();
    activityAbort = ctrl;
    let backoff = ACTIVITY_BACKOFF_INITIAL_MS;
    while (!ctrl.signal.aborted) {
      try {
        for await (const ev of chatClient.streamChatActivity({}, { signal: ctrl.signal })) {
          backoff = ACTIVITY_BACKOFF_INITIAL_MS;
          if (ev.kind.case === "turnStarted") busyChats.add(ev.chatId);
          else if (ev.kind.case === "turnEnded") busyChats.delete(ev.chatId);
        }
        return;
      } catch {
        if (ctrl.signal.aborted) return;
        void loadChats(true);
        await new Promise((r) => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, ACTIVITY_BACKOFF_MAX_MS);
      }
    }
  }

  onMount(() => {
    void loadChats();
    void subscribeChatActivity();
  });
</script>

<main class="flex h-[calc(100vh-3.5rem)] min-h-0">
  <ChatList chats={chats} selectedId={selected?.id ?? null} loading={loading} busyChats={busyChats} onrefresh={loadChats} onselect={selectChat} oncreate={createChat} onrename={renameChat} ondelete={deleteChat} />

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
