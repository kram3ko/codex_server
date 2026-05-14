<script lang="ts">
  import { Sparkles, UserRound } from "lucide-svelte";
  import { tick } from "svelte";

  import type { Attachment as ChatAttachment } from "../../gen/codex/v1/chat_pb";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import Attachment from "./Attachment.svelte";
  import CompletedTools from "./CompletedTools.svelte";
  import Message from "./Message.svelte";
  import ToolCall, { type ToolEvent } from "./ToolCall.svelte";

  let {
    messages,
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
    messages: ChatMessage[];
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

  function clientIdOf(message: ChatMessage): string | undefined {
    const meta = message.meta as Record<string, unknown> | undefined;
    const cid = meta?.client_id;
    return typeof cid === "string" ? cid : undefined;
  }

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

  // IntersectionObserver на top-sentinel — спрацьовує один раз коли він
  // в'їжджає у viewport, замість шумного `scrollTop<100` на кожен onscroll.
  $effect(() => {
    if (!container || !topSentinel) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (!entries[0].isIntersecting) return;
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

  // Після prepend — відновити viewport через delta-correction. Інакше — snap
  // до низу при `stickToBottom`.
  $effect(() => {
    void messages;
    void tools;
    void attachments;
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
    tick().then(() => {
      if (container) container.scrollTop = container.scrollHeight;
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
      <article class="msg-in flex gap-3 {isUser ? 'justify-end' : 'justify-start'}">
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
