<script lang="ts">
  import { Plus, Save, Search, Trash2 } from "lucide-svelte";
  import { onMount } from "svelte";

  import type { Note } from "../../gen/codex/v1/notes_pb";
  import { notesClient } from "../../shared/lib/clients";
  import { formatTime } from "../../shared/lib/time";

  let notes = $state<Note[]>([]);
  let selected = $state<Note | null>(null);
  let query = $state("");
  let title = $state("");
  let body = $state("");
  let tags = $state("");
  let error = $state("");
  let busy = $state(false);

  async function load() {
    busy = true;
    error = "";
    try {
      if (query.trim()) {
        const response = await notesClient.searchNotes({
          query: query.trim(),
          pagination: { limit: 100 }
        });
        notes = response.hits.map((hit) => hit.note).filter(Boolean) as Note[];
      } else {
        const response = await notesClient.listNotes({ pagination: { limit: 100 } });
        notes = [...response.notes];
      }
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to load notes";
    } finally {
      busy = false;
    }
  }

  function edit(note: Note) {
    selected = note;
    title = note.title;
    body = note.body;
    tags = note.tags.join(", ");
  }

  function fresh() {
    selected = null;
    title = "";
    body = "";
    tags = "";
  }

  async function save() {
    if (!title.trim() && !body.trim()) {
      return;
    }
    busy = true;
    try {
      await notesClient.saveNote({
        id: selected?.id,
        title: title.trim() || "Untitled",
        body,
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean)
      });
      fresh();
      await load();
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Save failed";
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (!selected) {
      return;
    }
    busy = true;
    try {
      await notesClient.deleteNote({ id: selected.id });
      fresh();
      await load();
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Delete failed";
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    void load();
  });
</script>

<main class="notes-shell grid h-[calc(100vh-3.5rem)] min-h-0 grid-cols-[320px_1fr]">
  <aside class="notes-sidebar min-h-0">
    <div class="notes-toolbar flex h-12 items-center gap-2 px-3">
      <div class="relative flex-1">
        <Search class="absolute left-2 top-2.5 text-[var(--notes-muted)]" size={15} />
        <input
          class="notes-input h-9 w-full rounded-md pl-8 pr-2 text-sm outline-none"
          bind:value={query}
          onkeydown={(event) => event.key === "Enter" && load()}
        />
      </div>
      <button class="notes-icon-button grid size-9 place-items-center rounded-md transition" type="button" title="New note" onclick={fresh}>
        <Plus size={16} />
      </button>
    </div>

    {#if error}
      <div class="notes-error px-3 py-2 text-sm">{error}</div>
    {/if}

    <div class="min-h-0 overflow-y-auto p-2">
      {#each notes as note (note.id.toString())}
        <button
          class="note-row mb-1 w-full rounded-md px-3 py-2 text-left transition"
          class:selected={selected?.id === note.id}
          type="button"
          onclick={() => edit(note)}
        >
          <div class="truncate text-sm font-medium text-[var(--notes-text)]">{note.title}</div>
          <div class="mt-1 text-xs text-[var(--notes-muted)]">{formatTime(note.updatedAt)}</div>
        </button>
      {/each}
    </div>
  </aside>

  <section class="notes-editor flex min-h-0 flex-col">
    <div class="notes-toolbar flex h-12 items-center justify-between px-4">
      <h2 class="text-sm font-semibold">{selected ? "Edit note" : "New note"}</h2>
      <div class="flex gap-2">
        {#if selected}
          <button class="notes-delete grid size-9 place-items-center rounded-md transition" type="button" title="Delete" onclick={remove}>
            <Trash2 size={16} />
          </button>
        {/if}
        <button class="notes-save flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium transition disabled:opacity-50" disabled={busy} type="button" onclick={save}>
          <Save size={15} />
          Save
        </button>
      </div>
    </div>

    <div class="grid min-h-0 flex-1 grid-rows-[auto_auto_1fr] gap-3 p-4">
      <input
        class="notes-field h-11 rounded-md px-3 text-lg font-semibold outline-none"
        bind:value={title}
        placeholder="Title"
      />
      <input
        class="notes-field h-10 rounded-md px-3 text-sm outline-none"
        bind:value={tags}
        placeholder="tags, comma-separated"
      />
      <textarea
        class="notes-field min-h-0 resize-none rounded-md p-3 leading-7 outline-none"
        bind:value={body}
        placeholder="Note body"
      ></textarea>
    </div>
  </section>
</main>

<style>
  .notes-shell {
    --notes-text: var(--color-text);
    --notes-muted: color-mix(in oklch, var(--color-text) 62%, transparent);
    --notes-panel-bg: color-mix(in oklch, var(--color-surface) 48%, transparent);
    --notes-editor-bg: color-mix(in oklch, var(--color-surface) 30%, transparent);
    --notes-toolbar-bg: color-mix(in oklch, var(--color-surface) 46%, transparent);
    --notes-row-bg: color-mix(in oklch, var(--color-surface) 18%, transparent);
    --notes-row-hover: color-mix(in oklch, var(--color-surface-2) 44%, transparent);
    --notes-row-selected: color-mix(in oklch, var(--color-accent-soft) 44%, var(--color-surface) 22%);
    --notes-field-bg: color-mix(in oklch, var(--color-surface) 74%, transparent);
    --notes-border: color-mix(in oklch, var(--color-border) 66%, transparent);
    --notes-soft-border: color-mix(in oklch, var(--color-border) 46%, transparent);

    color: var(--notes-text);
  }

  :global(:root[data-theme="light"]) .notes-shell {
    --notes-muted: color-mix(in oklch, var(--color-text) 58%, white 16%);
    --notes-panel-bg: color-mix(in oklch, var(--color-surface) 78%, transparent);
    --notes-editor-bg: color-mix(in oklch, var(--color-surface) 68%, transparent);
    --notes-toolbar-bg: color-mix(in oklch, var(--color-surface) 86%, transparent);
    --notes-row-bg: color-mix(in oklch, var(--color-surface) 56%, transparent);
    --notes-row-hover: color-mix(in oklch, var(--color-surface-2) 78%, transparent);
    --notes-row-selected: color-mix(in oklch, var(--color-accent-soft) 34%, var(--color-surface) 72%);
    --notes-field-bg: color-mix(in oklch, var(--color-surface) 88%, transparent);
  }

  .notes-sidebar,
  .notes-editor,
  .notes-toolbar {
    backdrop-filter: blur(14px) saturate(130%);
    -webkit-backdrop-filter: blur(14px) saturate(130%);
  }

  .notes-sidebar {
    background: var(--notes-panel-bg);
    border-right: 1px solid var(--notes-border);
  }

  .notes-editor {
    background: var(--notes-editor-bg);
  }

  .notes-toolbar {
    background: var(--notes-toolbar-bg);
    border-bottom: 1px solid var(--notes-border);
  }

  .notes-input,
  .notes-field {
    color: var(--notes-text);
    border: 1px solid var(--notes-soft-border);
    background: var(--notes-field-bg);
  }

  .notes-input::placeholder,
  .notes-field::placeholder {
    color: var(--notes-muted);
  }

  .notes-input:focus,
  .notes-field:focus {
    border-color: var(--color-accent);
    box-shadow: 0 0 0 2px color-mix(in oklch, var(--color-accent-soft) 70%, transparent);
  }

  .notes-icon-button {
    color: var(--notes-muted);
  }
  .notes-icon-button:hover {
    color: var(--notes-text);
    background: var(--notes-row-hover);
  }

  .note-row {
    background: var(--notes-row-bg);
    border: 1px solid transparent;
  }
  .note-row:hover {
    background: var(--notes-row-hover);
    border-color: var(--notes-soft-border);
  }
  .note-row.selected {
    background: var(--notes-row-selected);
    border-color: color-mix(in oklch, var(--color-accent) 48%, transparent);
  }

  .notes-error {
    color: var(--color-danger);
    background: color-mix(in oklch, var(--color-danger) 12%, var(--notes-toolbar-bg));
    border-bottom: 1px solid color-mix(in oklch, var(--color-danger) 36%, transparent);
  }

  .notes-delete {
    color: var(--color-danger);
    border: 1px solid color-mix(in oklch, var(--color-danger) 42%, transparent);
    background: color-mix(in oklch, var(--color-danger) 9%, var(--notes-field-bg));
  }
  .notes-delete:hover {
    background: color-mix(in oklch, var(--color-danger) 16%, var(--notes-field-bg));
  }

  .notes-save {
    color: var(--color-bg);
    background: var(--color-accent);
  }
  .notes-save:hover {
    filter: brightness(1.08);
  }
</style>
