<script lang="ts">
  import { Check, Copy } from "lucide-svelte";

  import CompletedTools from "./CompletedTools.svelte";
  import HistoricalAudio from "./HistoricalAudio.svelte";
  import HistoricalFile from "./HistoricalFile.svelte";
  import HistoricalImage from "./HistoricalImage.svelte";
  import type { ToolEvent } from "./toolEvent";
  import { renderMarkdown } from "./markdown";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import { formatTime } from "../../shared/lib/time";

  let {
    message,
    streaming = false,
    startedAt,
    lastActivityAt,
    idleTimeoutMs,
    currentToolName
  }: {
    message: ChatMessage;
    streaming?: boolean;
    startedAt?: number;
    lastActivityAt?: number;
    idleTimeoutMs?: number;
    currentToolName?: string;
  } = $props();

  const isUser = $derived(message.role === 1);
  const empty = $derived(streaming && !message.text);

  // protobuf-es v2 — google.protobuf.Struct рендериться як plain JsonObject, не клас.
  const metaJson = $derived(message.meta as Record<string, unknown> | undefined);
  const isSteered = $derived(metaJson?.steered === true);
  const isPartial = $derived(metaJson?.partial === true);
  const isInterruptedEmpty = $derived(isPartial && !message.text);
  const uploadIds = $derived.by((): number[] => idsFromMeta(metaJson?.upload_ids));
  const audioUploadIds = $derived.by((): number[] =>
    idsFromMeta(metaJson?.audio_upload_ids)
  );
  const fileUploadIds = $derived.by((): number[] =>
    idsFromMeta(metaJson?.file_upload_ids)
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

  let now = $state(Date.now());
  $effect(() => {
    if (!streaming) return;
    now = Date.now();
    const id = setInterval(() => { now = Date.now(); }, 250);
    return () => clearInterval(id);
  });

  const elapsed = $derived(
    streaming && startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0
  );
  const idleS = $derived(
    streaming && lastActivityAt ? Math.max(0, Math.floor((now - lastActivityAt) / 1000)) : 0
  );
  const idleMaxS = $derived(idleTimeoutMs ? Math.floor(idleTimeoutMs / 1000) : 0);
  const idleWarn = $derived(idleMaxS > 0 && idleS >= idleMaxS - 60);

  function fmtMSS(s: number) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r.toString().padStart(2, "0")}`;
  }

  function fmtElapsed(s: number) {
    return s < 60 ? `${s}s` : fmtMSS(s);
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

<div
  class="rounded-2xl px-4 py-3 transition-colors {isUser
    ? 'bg-[oklch(64%_0.16_230/0.42)] border border-[oklch(70%_0.16_230/0.55)] backdrop-blur-md'
    : 'glass-bubble'}"
>
    <div class="mb-1.5 flex items-center gap-2 text-[11px] text-[var(--color-text-muted)]">
      <span class="font-medium">{isUser ? "You" : "Codex"}</span>
      {#if isSteered}
        <span
          class="rounded-full border border-[oklch(70%_0.16_230/0.45)] bg-[oklch(64%_0.16_230/0.18)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-user)]"
          title="Sent into the running response"
        >
          steered
        </span>
      {/if}
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
              <span class="rounded bg-[var(--color-bg)]/30 px-1 font-mono tabular-nums" title="Total turn time">{fmtElapsed(elapsed)}</span>
            {/if}
            {#if lastActivityAt && idleMaxS > 0}
              <span
                class="rounded px-1 font-mono tabular-nums {idleWarn ? 'bg-[oklch(72%_0.18_50/0.85)] text-[var(--color-bg)]' : 'bg-[var(--color-bg)]/30'}"
                title="Time since last stream activity — resets on each event, timeout at {fmtMSS(idleMaxS)}"
              >idle {fmtMSS(idleS)}/{fmtMSS(idleMaxS)}</span>
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
    {:else if isInterruptedEmpty}
      <div class="py-2 text-[12px] italic text-[var(--color-text-muted)]">
        Codex was thinking — turn interrupted before any output.
      </div>
    {:else}
      <div class="markdown text-[1rem] leading-[1.7]">
        {@html renderMarkdown(message.text || "")}{#if streaming}<span
            class="ml-[2px] inline-block h-[1em] w-[7px] -translate-y-px rounded-sm bg-[var(--color-accent)] align-middle animate-blink"
          ></span>{/if}
      </div>

      {#if historicalCalls.length}
        <div class="mt-3">
          <CompletedTools tools={historicalCalls} label="tool calls" />
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

      {#if fileUploadIds.length}
        <div class="mt-3 space-y-2">
          {#each fileUploadIds as uploadId (uploadId)}
            <HistoricalFile {uploadId} />
          {/each}
        </div>
      {/if}
    {/if}
</div>
