<script lang="ts">
  import { Sparkles, UserRound } from "lucide-svelte";
  import { tick } from "svelte";

  import type { Attachment as ChatAttachment } from "../../gen/codex/v1/chat_pb";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import Attachment from "./Attachment.svelte";
  import CompletedTools from "./CompletedTools.svelte";
  import { clientIdOf, type ScrollIntent, type ScrollTarget } from "./liveTurn";
  import Message from "./Message.svelte";
  import type { ToolEvent } from "./toolEvent";
  import ToolCall from "./ToolCall.svelte";

  let {
    chatId,
    messages,
    scrollIntent,
    streamingClientId,
    tools,
    attachments,
    draftStartedAt,
    lastActivityAt,
    idleTimeoutMs,
    loadingOlder = false,
    hasMoreOlder = false,
    onloadolder
  }: {
    chatId: bigint | null;
    messages: ChatMessage[];
    scrollIntent: ScrollIntent;
    streamingClientId: string | null;
    tools: ToolEvent[];
    attachments: ChatAttachment[];
    draftStartedAt?: number;
    lastActivityAt?: number;
    idleTimeoutMs?: number;
    loadingOlder?: boolean;
    hasMoreOlder?: boolean;
    onloadolder?: () => void;
  } = $props();

  function messageKey(message: ChatMessage): string {
    return clientIdOf(message) ?? message.id.toString();
  }

  let container = $state<HTMLDivElement | null>(null);
  let topSentinel = $state<HTMLDivElement | null>(null);
  let stickToBottom = $state(true);

  const runningTools = $derived(tools.filter((t) => t.status === "running"));
  const completedTools = $derived(tools.filter((t) => t.status !== "running"));
  const currentToolName = $derived(runningTools[0]?.name);

  let lastScrollTop = 0;
  let lastMessagesLen = 0;
  let lastFirstMessageId: bigint | null = null;
  let lastChatId: bigint | null = null;
  let lastScrollIntentSeq = 0;
  // Запит на scroll від батька, що його застосує єдиний layout-ефект нижче
  // (інакше append-stick і intent планували б два конкурентні tick().then і
  // другий затирав би перший).
  let pendingScrollTarget: ScrollTarget | null = null;
  // Snapshot перед load-older — після того як прийшли нові, відновлюємо
  // scrollTop = newScrollHeight - prevScrollHeight + prevScrollTop. Гарантоване
  // збереження viewport на тому ж повідомленні (надійніше browser anchor).
  let scrollAnchor: { scrollHeight: number; scrollTop: number } | null = null;

  function onscroll() {
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    if (scrollTop < lastScrollTop) {
      stickToBottom = false;
    } else if (scrollHeight - scrollTop - clientHeight < 50) {
      stickToBottom = true;
    }
    lastScrollTop = scrollTop;
  }

  function scrollToBottom() {
    if (!container) return;
    container.scrollTop = container.scrollHeight;
  }

  function scrollToStream() {
    if (!container) return;
    const streaming = container?.querySelector<HTMLElement>('[data-streaming="true"]');
    if (streaming) {
      const containerRect = container.getBoundingClientRect();
      const streamingRect = streaming.getBoundingClientRect();
      container.scrollTop += streamingRect.bottom - containerRect.bottom;
      return;
    }
    scrollToBottom();
  }

  $effect(() => {
    if (chatId === lastChatId) return;
    lastChatId = chatId;
    stickToBottom = true;
    lastScrollTop = 0;
    lastMessagesLen = 0;
    lastFirstMessageId = null;
    scrollAnchor = null;
  });

  $effect(() => {
    const intent = scrollIntent;
    if (intent.seq === lastScrollIntentSeq) return;
    lastScrollIntentSeq = intent.seq;
    stickToBottom = true;
    scrollAnchor = null;
    pendingScrollTarget = intent.target;
  });

  // IntersectionObserver на top-sentinel — спрацьовує один раз коли він
  // в'їжджає у viewport, замість шумного `scrollTop<100` на кожен onscroll.
  $effect(() => {
    if (!container || !topSentinel) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries[0]?.isIntersecting) return;
        if (!hasMoreOlder || loadingOlder || !container) return;
        scrollAnchor = {
          scrollHeight: container.scrollHeight,
          scrollTop: container.scrollTop
        };
        onloadolder?.();
      },
      { root: container, rootMargin: "100px 0px 0px 0px" }
    );
    io.observe(topSentinel);
    return () => io.disconnect();
  });

  // Append (новий send / done) → стик-вниз. Prepend (load-older) → НЕ стикаємо.
  $effect(() => {
    const firstId = messages[0]?.id ?? null;
    const prepended = lastFirstMessageId !== null && firstId !== lastFirstMessageId;
    if (messages.length > lastMessagesLen && !prepended) {
      stickToBottom = true;
    }
    lastMessagesLen = messages.length;
    lastFirstMessageId = firstId;
  });

  // Єдина точка фактичного скролу: load-older → delta-correction; інакше при
  // stickToBottom — до streaming-рядка (pending intent "stream") або до низу.
  // Один tick().then гарантує, що intent-target не затреться append-snap-ом.
  $effect(() => {
    void messages;
    void tools;
    void attachments;
    void scrollIntent;
    if (scrollAnchor && container) {
      const anchor = scrollAnchor;
      scrollAnchor = null;
      tick().then(() => {
        if (!container) return;
        container.scrollTop = container.scrollHeight - anchor.scrollHeight + anchor.scrollTop;
      });
      return;
    }
    if (!stickToBottom) return;
    const target = pendingScrollTarget;
    pendingScrollTarget = null;
    tick().then(() => {
      if (!container) return;
      if (target === "stream") scrollToStream();
      else scrollToBottom();
      lastScrollTop = container.scrollTop;
    });
  });
</script>

<div
  bind:this={container}
  {onscroll}
  class="min-h-0 flex-1 overflow-y-auto px-5 py-4"
>
  <div class="mx-auto flex max-w-5xl flex-col gap-4">
    <!-- Sentinel for IntersectionObserver — triggers loadOlder when visible. -->
    <div bind:this={topSentinel} aria-hidden="true"></div>
    {#if loadingOlder}
      <div class="grid place-items-center py-2 text-[11px] text-[var(--color-text-muted)]">
        loading older…
      </div>
    {/if}
    {#each messages as message (messageKey(message))}
      {@const streaming = clientIdOf(message) === streamingClientId}
      {@const isUser = message.role === 1}
      <article
        data-streaming={streaming ? "true" : undefined}
        class="msg-in flex gap-3 {isUser ? 'justify-end' : 'justify-start'}"
      >
        {#if !isUser}
          <div
            class="mt-1 grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-[var(--color-bg)] shadow-md shadow-[oklch(72%_0.18_175/0.25)] {streaming ? 'animate-pulse-glow' : ''}"
          >
            <Sparkles size={15} strokeWidth={2.5} />
          </div>
        {/if}
        <div class="flex min-w-0 max-w-bubble flex-col gap-2">
          <Message
            {message}
            {streaming}
            startedAt={streaming ? draftStartedAt : undefined}
            lastActivityAt={streaming ? lastActivityAt : undefined}
            idleTimeoutMs={streaming ? idleTimeoutMs : undefined}
            currentToolName={streaming ? currentToolName : undefined}
          />
          {#if streaming && tools.length}
            <div class="space-y-2">
              {#each runningTools as tool (tool.id)}
                <ToolCall event={tool} />
              {/each}
              <CompletedTools tools={completedTools} />
            </div>
          {/if}
          {#if streaming && attachments.length}
            <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
              {#each attachments as attachment (`${attachment.kind}:${attachment.source}`)}
                <Attachment {attachment} />
              {/each}
            </div>
          {/if}
        </div>
        {#if isUser}
          <div class="mt-1 grid size-8 shrink-0 place-items-center rounded-lg border border-[oklch(70%_0.16_230/0.35)] bg-[var(--color-user-soft)] text-[var(--color-user)]">
            <UserRound size={15} />
          </div>
        {/if}
      </article>
    {/each}
  </div>
</div>
