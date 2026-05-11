<script lang="ts">
  import { Check, Copy, Sparkles, UserRound } from "lucide-svelte";

  import HistoricalAudio from "./HistoricalAudio.svelte";
  import HistoricalImage from "./HistoricalImage.svelte";
  import ToolCall, { type ToolEvent } from "./ToolCall.svelte";
  import { renderMarkdown } from "./markdown";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import { formatTime } from "../../shared/lib/time";

  let {
    message,
    streaming = false,
    startedAt,
    currentToolName
  }: {
    message: ChatMessage;
    streaming?: boolean;
    startedAt?: number;
    currentToolName?: string;
  } = $props();

  const isUser = $derived(message.role === 1);
  const empty = $derived(streaming && !message.text);

  // protobuf-es v2 — google.protobuf.Struct рендериться як plain JsonObject, не клас.
  const metaJson = $derived(message.meta as Record<string, unknown> | undefined);
  const uploadIds = $derived.by((): number[] => idsFromMeta(metaJson?.upload_ids));
  const audioUploadIds = $derived.by((): number[] =>
    idsFromMeta(metaJson?.audio_upload_ids)
  );
  const historicalCalls = $derived.by((): ToolEvent[] => {
    const raw = metaJson?.calls;
    if (!Array.isArray(raw)) return [];
    return raw.map((entry, idx) => {
      const obj = (entry ?? {}) as Record<string, unknown>;
      return {
        id: `hist-${message.id}-${idx}`,
        name: typeof obj.name === "string" ? obj.name : "tool",
        args: obj.args,
        status: "done"
      };
    });
  });

  function idsFromMeta(value: unknown): number[] {
    if (!Array.isArray(value)) return [];
    return value.map((v) => Number(v)).filter((n) => Number.isFinite(n));
  }

  let elapsed = $state(0);
  $effect(() => {
    if (!streaming || !startedAt) {
      elapsed = 0;
      return;
    }
    elapsed = Math.floor((Date.now() - startedAt) / 1000);
    const id = setInterval(() => {
      elapsed = Math.floor((Date.now() - startedAt) / 1000);
    }, 250);
    return () => clearInterval(id);
  });

  function fmtElapsed(s: number) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return m > 0 ? `${m}:${r.toString().padStart(2, "0")}` : `${s}s`;
  }

  let copied = $state(false);
  let copyTimer: ReturnType<typeof setTimeout> | null = null;
  async function copyText() {
    if (!message.text) return;
    await navigator.clipboard.writeText(message.text);
    copied = true;
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => (copied = false), 1500);
  }
</script>

<article class="msg-in flex gap-3 {isUser ? 'justify-end' : 'justify-start'}">
  {#if !isUser}
    <div
      class="mt-1 grid size-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-[var(--color-bg)] shadow-md shadow-[oklch(72%_0.18_175/0.25)] {streaming ? 'animate-pulse-glow' : ''}"
    >
      <Sparkles size={15} strokeWidth={2.5} />
    </div>
  {/if}

  <div
    class="max-w-[min(760px,80%)] rounded-2xl px-4 py-3 transition-colors {isUser
      ? 'bg-[oklch(64%_0.16_230/0.42)] border border-[oklch(70%_0.16_230/0.55)] backdrop-blur-md'
      : 'glass-bubble'}"
  >
    <div class="mb-1.5 flex items-center gap-2 text-[11px] text-[var(--color-text-muted)]">
      <span class="font-medium">{isUser ? "You" : "Codex"}</span>
      {#if message.createdAt}
        <span>·</span>
        <span>{formatTime(message.createdAt)}</span>
      {/if}
      <div class="ml-auto flex items-center gap-2">
        {#if streaming}
          <span class="flex items-center gap-1.5 rounded-full bg-[var(--color-accent)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-bg)] shadow-md shadow-[oklch(72%_0.18_175/0.3)]">
            <span class="size-1.5 rounded-full bg-[var(--color-bg)] animate-pulse"></span>
            {currentToolName ?? "streaming"}
            {#if startedAt}
              <span class="rounded bg-[var(--color-bg)]/30 px-1 font-mono tabular-nums">{fmtElapsed(elapsed)}</span>
            {/if}
          </span>
        {/if}
        {#if message.text}
          <button
            type="button"
            class="grid size-6 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-text)]"
            title={copied ? "Copied" : "Copy"}
            onclick={copyText}
          >
            {#if copied}
              <Check size={13} />
            {:else}
              <Copy size={13} />
            {/if}
          </button>
        {/if}
      </div>
    </div>

    {#if empty}
      <div class="flex items-center gap-2 py-2">
        <span class="size-2 rounded-full bg-[var(--color-accent)] animate-thinking" style="animation-delay:0ms"></span>
        <span class="size-2 rounded-full bg-[var(--color-accent)] animate-thinking" style="animation-delay:160ms"></span>
        <span class="size-2 rounded-full bg-[var(--color-accent)] animate-thinking" style="animation-delay:320ms"></span>
      </div>
    {:else}
      <div class="markdown text-[1rem] leading-[1.7]">
        {@html renderMarkdown(message.text || "")}{#if streaming}<span
            class="ml-[2px] inline-block h-[1em] w-[7px] -translate-y-px rounded-sm bg-[var(--color-accent)] align-middle animate-blink"
          ></span>{/if}
      </div>

      {#if historicalCalls.length}
        <div class="mt-3 space-y-2">
          {#each historicalCalls as call (call.id)}
            <ToolCall event={call} />
          {/each}
        </div>
      {/if}

      {#if uploadIds.length}
        <div class="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
          {#each uploadIds as uploadId (uploadId)}
            <HistoricalImage {uploadId} />
          {/each}
        </div>
      {/if}

      {#if audioUploadIds.length}
        <div class="mt-3 space-y-2">
          {#each audioUploadIds as uploadId (uploadId)}
            <HistoricalAudio {uploadId} />
          {/each}
        </div>
      {/if}
    {/if}
  </div>

  {#if isUser}
    <div class="mt-1 grid size-8 shrink-0 place-items-center rounded-lg border border-[oklch(70%_0.16_230/0.35)] bg-[var(--color-user-soft)] text-[var(--color-user)]">
      <UserRound size={15} />
    </div>
  {/if}
</article>
