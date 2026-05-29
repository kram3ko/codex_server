<script lang="ts">
  import { tick } from "svelte";
  import { Loader2, MessagesSquare, Pencil, Plus, RefreshCw, Trash2 } from "lucide-svelte";

  import type { Chat } from "../../gen/codex/v1/chat_pb";
  import { formatTime } from "../../shared/lib/time";
  import Usage from "./Usage.svelte";

  let {
    chats,
    selectedId,
    loading,
    busyChats,
    onrefresh,
    onselect,
    oncreate,
    onrename,
    ondelete
  }: {
    chats: Chat[];
    selectedId: bigint | null;
    loading: boolean;
    busyChats: ReadonlySet<bigint>;
    onrefresh: () => void;
    onselect: (chat: Chat) => void;
    oncreate: (title: string) => void;
    onrename: (chat: Chat, title: string) => void;
    ondelete: (chat: Chat) => void;
  } = $props();

  let creating = $state(false);
  let createDraft = $state("");
  let createInput = $state<HTMLInputElement | null>(null);

  let editingId = $state<bigint | null>(null);
  let editDraft = $state("");
  let editInput = $state<HTMLInputElement | null>(null);

  async function startCreate() {
    creating = true;
    createDraft = "";
    await tick();
    createInput?.focus();
  }

  function cancelCreate() {
    creating = false;
    createDraft = "";
  }

  function commitCreate(e?: Event) {
    e?.preventDefault();
    // Idempotent: onsubmit + onblur fire back-to-back when the input unmounts;
    // without this guard each Enter would create two chats.
    if (!creating) return;
    const draft = createDraft.trim();
    cancelCreate();
    oncreate(draft);
  }

  async function startEdit(chat: Chat) {
    editingId = chat.id;
    editDraft = chat.title ?? "";
    await tick();
    editInput?.focus();
    editInput?.select();
  }

  function cancelEdit() {
    editingId = null;
    editDraft = "";
  }

  function commitEdit(chat: Chat, e?: Event) {
    e?.preventDefault();
    // Same idempotency reason as commitCreate — submit + blur duplicate.
    if (editingId !== chat.id) return;
    const next = editDraft.trim();
    cancelEdit();
    if (next && next !== (chat.title ?? "")) onrename(chat, next);
  }

  function confirmDelete(chat: Chat) {
    const label = chat.title || `Chat #${chat.id}`;
    if (window.confirm(`Delete "${label}"? Messages history will be lost.`)) {
      ondelete(chat);
    }
  }
</script>

<aside class="chat-sidebar flex min-h-0 w-72 flex-col">
  <header class="chat-sidebar-header flex h-14 items-center justify-between px-4">
    <div class="flex items-center gap-2.5">
      <h2 class="text-[12px] font-semibold uppercase tracking-[0.08em] text-[var(--chat-sidebar-muted)]">Chats</h2>
      {#if chats.length > 0}
        <span class="chat-count grid min-w-6 place-items-center rounded-full px-2 py-0.5 text-[11px] font-semibold">{chats.length}</span>
      {/if}
    </div>
    <div class="flex items-center gap-1.5">
      <button
        class="icon-button grid size-9 place-items-center rounded-lg transition disabled:cursor-not-allowed disabled:opacity-45"
        title="Refresh"
        type="button"
        onclick={onrefresh}
        disabled={loading}
      >
        <RefreshCw size={17} class={loading ? "animate-spin" : ""} />
      </button>
      <button
        class="icon-button primary grid size-9 place-items-center rounded-lg transition"
        title="New chat"
        type="button"
        onclick={startCreate}
      >
        <Plus size={18} />
      </button>
    </div>
  </header>

  <div class="min-h-0 flex-1 overflow-y-auto px-3 py-3">
    {#if creating}
      <form class="mb-2" onsubmit={commitCreate}>
        <input
          bind:this={createInput}
          bind:value={createDraft}
          class="chat-input h-11 w-full rounded-lg px-3 text-sm outline-none"
          placeholder="Chat name… (empty = auto)"
          onkeydown={(e) => e.key === "Escape" && cancelCreate()}
          onblur={() => commitCreate()}
        />
      </form>
    {/if}

    {#if chats.length === 0 && !creating}
      <div class="empty-chat m-2 flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center text-sm">
        <MessagesSquare size={20} />
        <span>No chats yet</span>
      </div>
    {:else}
      {#each chats as chat (chat.id.toString())}
        {@const isSelected = selectedId === chat.id}
        {@const isEditing = editingId === chat.id}
        <div
          class="chat-row group relative mb-2 flex min-h-[72px] w-full items-stretch gap-2 overflow-hidden rounded-lg border text-left transition"
          class:selected={isSelected || isEditing}
        >
          <div
            class="chat-row-accent w-1 shrink-0 transition"
          ></div>
          {#if isEditing}
            <div class="flex min-w-0 flex-1 flex-col justify-center py-3 pl-2 pr-1">
              <form class="contents" onsubmit={(e) => commitEdit(chat, e)}>
                <input
                  bind:this={editInput}
                  bind:value={editDraft}
                  class="chat-input h-8 w-full rounded-md px-2 text-[15px] font-semibold outline-none"
                  onkeydown={(e) => e.key === "Escape" && cancelEdit()}
                  onblur={() => commitEdit(chat)}
                />
              </form>
              <span class="mt-1 text-[12px] font-medium leading-5 text-[var(--chat-sidebar-muted)]">{formatTime(chat.lastMsgAt)}</span>
            </div>
          {:else}
            <button
              class="flex min-w-0 flex-1 flex-col items-start justify-center py-3 pl-2 pr-1 text-left"
              type="button"
              onclick={() => onselect(chat)}
              ondblclick={() => startEdit(chat)}
            >
              <span class="chat-title flex w-full items-center gap-1.5 truncate text-[16px] font-semibold leading-6 transition">
                {#if busyChats.has(chat.id)}
                  <Loader2 size={13} class="shrink-0 animate-spin text-[var(--color-accent)]" />
                {/if}
                <span class="truncate">{chat.title || `Chat #${chat.id}`}</span>
              </span>
              <span class="mt-1 text-[12px] font-medium leading-5 text-[var(--chat-sidebar-muted)]">{formatTime(chat.lastMsgAt)}</span>
            </button>
          {/if}
          {#if !isEditing}
            <div class="flex w-10 shrink-0 flex-col items-center justify-center gap-1.5 pr-2 opacity-0 transition group-hover:opacity-100 {isSelected ? 'opacity-100' : ''}">
              <button
                class="mini-action grid size-7 place-items-center rounded-md transition"
                title="Rename"
                type="button"
                onclick={(e) => {
                  e.stopPropagation();
                  startEdit(chat);
                }}
              >
                <Pencil size={14} />
              </button>
              <button
                class="mini-action danger grid size-7 place-items-center rounded-md transition"
                title="Delete"
                type="button"
                onclick={(e) => {
                  e.stopPropagation();
                  confirmDelete(chat);
                }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          {/if}
        </div>
      {/each}
    {/if}
  </div>

  <Usage />
</aside>

<style>
  .chat-sidebar {
    --chat-sidebar-bg: color-mix(in oklch, var(--color-surface) 50%, transparent);
    --chat-sidebar-header-bg: color-mix(in oklch, var(--color-surface) 34%, transparent);
    --chat-sidebar-row-bg: color-mix(in oklch, var(--color-surface) 18%, transparent);
    --chat-sidebar-row-hover: color-mix(in oklch, var(--color-surface) 38%, transparent);
    --chat-sidebar-row-selected: color-mix(in oklch, var(--color-accent-soft) 42%, var(--color-surface) 24%);
    --chat-sidebar-border: color-mix(in oklch, var(--color-border) 72%, transparent);
    --chat-sidebar-soft-border: color-mix(in oklch, var(--color-border) 46%, transparent);
    --chat-sidebar-title: var(--color-text);
    --chat-sidebar-muted: color-mix(in oklch, var(--color-text) 68%, transparent);

    background: var(--chat-sidebar-bg);
    border-right: 1px solid var(--chat-sidebar-border);
    backdrop-filter: blur(14px) saturate(130%);
    -webkit-backdrop-filter: blur(14px) saturate(130%);
  }

  :global(:root[data-theme="light"]) .chat-sidebar {
    --chat-sidebar-bg: color-mix(in oklch, var(--color-surface) 76%, transparent);
    --chat-sidebar-header-bg: color-mix(in oklch, var(--color-surface) 84%, transparent);
    --chat-sidebar-row-bg: color-mix(in oklch, var(--color-surface) 54%, transparent);
    --chat-sidebar-row-hover: color-mix(in oklch, var(--color-surface-2) 76%, transparent);
    --chat-sidebar-row-selected: color-mix(in oklch, var(--color-accent-soft) 40%, var(--color-surface) 70%);
    --chat-sidebar-muted: color-mix(in oklch, var(--color-text) 62%, white 16%);
  }

  .chat-sidebar-header {
    background: var(--chat-sidebar-header-bg);
    border-bottom: 1px solid var(--chat-sidebar-border);
  }

  .chat-count,
  .empty-chat {
    color: var(--chat-sidebar-muted);
    border-color: var(--chat-sidebar-soft-border);
    background: color-mix(in oklch, var(--color-surface) 28%, transparent);
  }

  .icon-button {
    color: var(--chat-sidebar-muted);
  }
  .icon-button:hover {
    color: var(--color-text);
    background: color-mix(in oklch, var(--color-surface-2) 52%, transparent);
  }
  .icon-button.primary {
    color: var(--color-accent);
    border: 1px solid color-mix(in oklch, var(--color-accent) 58%, transparent);
    background: color-mix(in oklch, var(--color-accent-soft) 56%, transparent);
  }
  .icon-button.primary:hover {
    color: var(--color-accent);
    background: color-mix(in oklch, var(--color-accent-soft) 82%, transparent);
  }

  .chat-input {
    color: var(--color-text);
    border: 1px solid color-mix(in oklch, var(--color-accent) 54%, transparent);
    background: color-mix(in oklch, var(--color-surface) 78%, transparent);
  }
  .chat-input::placeholder {
    color: var(--chat-sidebar-muted);
  }
  .chat-input:focus {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 2px color-mix(in oklch, var(--color-accent-soft) 72%, transparent);
  }

  .chat-row {
    color: var(--color-text);
    border-color: transparent;
    background: var(--chat-sidebar-row-bg);
  }
  .chat-row:hover {
    border-color: var(--chat-sidebar-soft-border);
    background: var(--chat-sidebar-row-hover);
  }
  .chat-row.selected {
    border-color: color-mix(in oklch, var(--color-accent) 54%, transparent);
    background: var(--chat-sidebar-row-selected);
  }
  .chat-row-accent {
    background: transparent;
  }
  .chat-row:hover .chat-row-accent {
    background: color-mix(in oklch, var(--color-border) 70%, transparent);
  }
  .chat-row.selected .chat-row-accent {
    background: var(--color-accent);
  }
  .chat-title {
    color: var(--chat-sidebar-title);
  }

  .mini-action {
    color: var(--chat-sidebar-muted);
  }
  .mini-action:hover {
    color: var(--color-text);
    background: color-mix(in oklch, var(--color-surface-2) 58%, transparent);
  }
  .mini-action.danger:hover {
    color: var(--color-danger);
    background: color-mix(in oklch, var(--color-danger) 14%, transparent);
  }
</style>
