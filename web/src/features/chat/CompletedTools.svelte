<script lang="ts">
  import { ChevronDown, ChevronRight } from "lucide-svelte";

  import ToolCall, { type ToolEvent } from "./ToolCall.svelte";

  // Shared collapse-блок для пачки tool-events. Використовується:
  // - MessageList:    completed-pile активного турну
  // - Message:        historical message.meta.calls
  let {
    tools,
    label = "completed"
  }: {
    tools: ToolEvent[];
    label?: string;
  } = $props();

  let open = $state(false);
</script>

{#if tools.length}
  <div class="space-y-2">
    <button
      type="button"
      class="flex w-full items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-left text-xs text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.04)]"
      onclick={() => (open = !open)}
    >
      {#if open}
        <ChevronDown size={13} />
      {:else}
        <ChevronRight size={13} />
      {/if}
      <span>{tools.length} {label}</span>
    </button>
    {#if open}
      {#each tools as tool (tool.id)}
        <ToolCall event={tool} />
      {/each}
    {/if}
  </div>
{/if}
