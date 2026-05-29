<script lang="ts">
  import { ChevronDown, ChevronRight, Loader2, Wrench } from "lucide-svelte";

  import type { ToolError } from "../../gen/codex/v1/chat_pb";

  export type ToolEvent = {
    id: string;
    name: string;
    args?: unknown;
    text?: string;
    error?: ToolError;
    status: "running" | "done" | "error";
  };

  let { event }: { event: ToolEvent } = $props();
  let open = $state(false);
</script>

<div
  class="glass-bubble overflow-hidden rounded-xl text-sm"
>
  <button
    class="flex w-full items-center gap-2 px-3 py-2 text-left transition hover:bg-[oklch(96%_0.01_100/0.04)]"
    type="button"
    onclick={() => (open = !open)}
  >
    {#if open}
      <ChevronDown size={15} class="text-[var(--color-text-muted)]" />
    {:else}
      <ChevronRight size={15} class="text-[var(--color-text-muted)]" />
    {/if}
    <Wrench size={14} class="text-[var(--color-accent)]" />
    <span class="font-medium text-[var(--color-text)]">{event.name}</span>
    <span
      class="ml-auto flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium {event.status === 'error'
        ? 'bg-[var(--color-danger)]/15 text-[var(--color-danger)]'
        : event.status === 'done'
          ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
          : 'bg-[var(--color-user-soft)] text-[var(--color-user)]'}"
    >
      {#if event.status === "running"}
        <Loader2 size={11} class="animate-spin" />
      {/if}
      {event.status}
    </span>
  </button>
  {#if open}
    <div class="space-y-2 border-t border-[var(--color-border)] p-3 text-sm">
      {#if event.args}
        <pre class="overflow-x-auto rounded-lg bg-[oklch(12%_0.01_250)] p-3 text-xs text-[oklch(94%_0.01_100)]">{JSON.stringify(event.args, null, 2)}</pre>
      {/if}
      {#if event.text}
        <pre class="whitespace-pre-wrap rounded-lg bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text)]">{event.text}</pre>
      {/if}
      {#if event.error}
        <div class="flex flex-col gap-1">
          {#if event.error.code}
            <span class="self-start rounded-md bg-[var(--color-danger)]/15 px-1.5 py-0.5 text-[11px] font-mono text-[var(--color-danger)]">{event.error.code}</span>
          {/if}
          <p class="text-[var(--color-danger)]">{event.error.message}</p>
        </div>
      {/if}
    </div>
  {/if}
</div>
