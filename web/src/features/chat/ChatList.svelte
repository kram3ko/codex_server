<script lang="ts">
  import { MessagesSquare, Plus, RefreshCw } from "lucide-svelte";

  import type { Chat } from "../../gen/codex/v1/chat_pb";
  import { formatTime } from "../../shared/lib/time";

  let {
    chats,
    selectedId,
    loading,
    onrefresh,
    onselect
  }: {
    chats: Chat[];
    selectedId: bigint | null;
    loading: boolean;
    onrefresh: () => void;
    onselect: (chat: Chat) => void;
  } = $props();
</script>

<aside class="flex min-h-0 w-72 flex-col border-r border-[var(--color-border)] bg-[oklch(18%_0.014_250/0.4)] backdrop-blur">
  <div class="flex h-12 items-center justify-between border-b border-[var(--color-border)] px-3">
    <h2 class="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Chats</h2>
    <div class="flex gap-1">
      <button
        class="grid size-7 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-text)]"
        title="Refresh"
        type="button"
        onclick={onrefresh}
      >
        <RefreshCw size={14} class={loading ? "animate-spin" : ""} />
      </button>
      <button
        class="grid size-7 place-items-center rounded-md text-[var(--color-text-muted)] opacity-40"
        disabled
        title="New chat"
        type="button"
      >
        <Plus size={14} />
      </button>
    </div>
  </div>

  <div class="min-h-0 flex-1 overflow-y-auto p-2">
    {#if chats.length === 0}
      <div class="m-2 flex flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] p-6 text-center text-sm text-[var(--color-text-muted)]">
        <MessagesSquare size={20} />
        <span>No chats yet</span>
      </div>
    {:else}
      {#each chats as chat (chat.id.toString())}
        <button
          class="mb-1 block w-full rounded-lg border px-3 py-2.5 text-left transition {selectedId === chat.id
            ? 'border-[var(--color-accent)]/50 bg-[var(--color-accent-soft)] text-[var(--color-text)]'
            : 'border-transparent text-[var(--color-text)] hover:border-[var(--color-border)] hover:bg-[var(--color-surface)]/40'}"
          type="button"
          onclick={() => onselect(chat)}
        >
          <div class="truncate text-sm font-medium">{chat.title || `Chat #${chat.id}`}</div>
          <div class="mt-1 text-[11px] text-[var(--color-text-muted)]">{formatTime(chat.lastMsgAt)}</div>
        </button>
      {/each}
    {/if}
  </div>
</aside>
