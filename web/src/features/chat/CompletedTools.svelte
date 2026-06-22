<script lang="ts">
  import type { ToolEvent } from "./toolEvent";
  import ToolCall from "./ToolCall.svelte";

  // Shared collapse-блок для пачки tool-events. <details>/<summary> дає
  // accessibility (aria-expanded, keyboard) безкоштовно, без manual $state.
  let {
    tools,
    label = "completed"
  }: {
    tools: ToolEvent[];
    label?: string;
  } = $props();
</script>

{#if tools.length}
  <details class="space-y-2 [&[open]>summary>svg]:rotate-90">
    <summary
      class="flex w-full cursor-pointer list-none items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.04)]"
    >
      <svg
        class="size-3 transition-transform"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        stroke-width="2.5"
        aria-hidden="true"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
      </svg>
      <span>{tools.length} {label}</span>
    </summary>
    {#each tools as tool (tool.id)}
      <ToolCall event={tool} />
    {/each}
  </details>
{/if}
