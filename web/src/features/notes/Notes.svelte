<script lang="ts">
  import { ArrowLeft, Eye, Pencil, Plus, Save, Search, Trash2, X } from "lucide-svelte";
  import { onMount } from "svelte";
  import NoteEditor from "./NoteEditor.svelte";

  import type { Note } from "../../gen/codex/v1/notes_pb";
  import { notesClient } from "../../shared/lib/clients";
  import { formatTime } from "../../shared/lib/time";
  import { renderMarkdown } from "../chat/markdown";

  let notes = $state<Note[]>([]);
  let selected = $state<Note | null>(null);
  let query = $state("");
  let title = $state("");
  let body = $state("");
  let tags = $state("");
  let error = $state("");
  let busy = $state(false);
  let editing = $state(false);
  let showingNote = $state(false);
  let tagInput = $state("");
  let loadVersion = 0;
  const tagList = $derived(tags.split(",").map((tag) => tag.trim()).filter(Boolean));
  const dirty = $derived(title !== (selected?.title ?? "") || body !== (selected?.body ?? "") || tags !== (selected?.tags.join(", ") ?? "") || !!tagInput.trim());

  function canLeave() {
    return !busy && (!dirty || confirm("Discard unsaved changes?"));
  }

  export function canNavigate() { return canLeave(); }

  function addTag() {
    const next = tagInput.trim().replaceAll(",", "");
    if (next) tags = [...new Set([...tagList, next])].join(", ");
    tagInput = "";
  }


  function beforeUnload(event: BeforeUnloadEvent) {
    if (dirty) event.preventDefault();
  }

  async function load() {
    const version = ++loadVersion;
    busy = true;
    error = "";
    try {
      if (query.trim()) {
        const response = await notesClient.searchNotes({
          query: query.trim(),
          pagination: { limit: 100 }
        });
        if (version === loadVersion) notes = response.hits.flatMap((hit) => hit.note ? [hit.note] : []);
      } else {
        const response = await notesClient.listNotes({ pagination: { limit: 100 } });
        if (version === loadVersion) notes = [...response.notes];
      }
    } catch (exc) {
      if (version === loadVersion) error = exc instanceof Error ? exc.message : "Failed to load notes";
    } finally {
      if (version === loadVersion) busy = false;
    }
  }

  function edit(note: Note) {
    if (!canLeave()) return;
    selected = note;
    title = note.title;
    body = note.body;
    tags = note.tags.join(", ");
    editing = false;
    showingNote = true;
    tagInput = "";
  }

  function fresh() {
    if (!canLeave()) return;
    selected = null;
    title = "";
    body = "";
    tags = "";
    tagInput = "";
    editing = true;
    showingNote = true;
  }

  async function save() {
    if (busy) return;
    if (!title.trim() && !body.trim()) {
      return;
    }
    busy = true;
    error = "";
    try {
      addTag();
      const saved = await notesClient.saveNote({
        id: selected?.id,
        title: title.trim() || "Untitled",
        body,
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean)
      });
      selected = saved;
      title = saved.title;
      body = saved.body;
      tags = saved.tags.join(", ");
      editing = false;
      await load();
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Save failed";
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (!selected || busy || !confirm(`Delete "${selected.title}"?`)) {
      return;
    }
    busy = true;
    try {
      await notesClient.deleteNote({ id: selected.id });
      selected = null;
      title = "";
      body = "";
      tags = "";
      showingNote = false;
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

<svelte:window onbeforeunload={beforeUnload} />
<main class="notes-shell grid h-full min-h-0 grid-cols-[240px_minmax(0,1fr)]" class:showing-note={showingNote}>
  <aside class="notes-sidebar min-h-0">
    <div class="notes-toolbar flex h-12 items-center gap-2 px-3">
      <div class="relative flex-1">
        <Search class="absolute left-2 top-2.5 text-[var(--notes-muted)]" size={15} />
        <input
          class="notes-input h-9 w-full rounded-md pl-8 pr-2 text-sm outline-none"
          bind:value={query}
          aria-label="Search notes"
          placeholder="Search notes"
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

    <div class="min-h-0 flex-1 overflow-y-auto p-2">
      {#if !notes.length}<p class="p-3 text-sm text-[var(--notes-muted)]">{busy ? "Loading..." : "No notes found"}</p>{/if}
      {#each notes as note (note.id.toString())}
        <button
          class="note-row mb-1 w-full rounded-md px-3 py-2 text-left transition"
          class:selected={selected?.id === note.id}
          type="button"
          onclick={() => edit(note)}
        >
          <div class="truncate text-sm font-medium text-[var(--notes-text)]">{note.title}</div>
          <div class="mt-1 line-clamp-2 break-words text-xs text-[var(--notes-muted)]">{note.body.slice(0, 180)}</div>
          <div class="mt-1 text-xs text-[var(--notes-muted)]">{formatTime(note.updatedAt)}</div>
        </button>
      {/each}
    </div>
  </aside>

  <section class="notes-editor flex min-h-0 flex-col">
    <div class="notes-toolbar flex h-12 items-center justify-between px-4">
      <div class="flex min-w-0 items-center gap-2">
        <button class="notes-back" title="Back to notes" onclick={() => { if (canLeave()) { showingNote = false; title = selected?.title ?? ""; body = selected?.body ?? ""; tags = selected?.tags.join(", ") ?? ""; } }}><ArrowLeft size={16} /></button>
        <div class="min-w-0">
          <h2 class="truncate text-sm font-semibold">{showingNote ? title || "Untitled note" : "Notes"}</h2>
          {#if showingNote}<p class="truncate text-xs text-[var(--notes-muted)]" title={dirty ? "Unsaved changes" : selected ? "All changes saved" : "New note"}>{dirty ? "Unsaved changes" : selected ? "Saved" : "New note"}</p>{/if}
        </div>
      </div>
      <div class="flex shrink-0 gap-2">
        {#if showingNote}
          <button class="notes-icon-button grid size-9 place-items-center rounded-md" title={editing ? "Preview" : "Edit"} onclick={() => editing = !editing}>
            {#if editing}<Eye size={16} />{:else}<Pencil size={16} />{/if}
          </button>
        {/if}
        {#if selected}
          <button class="notes-delete grid size-9 place-items-center rounded-md transition" type="button" title="Delete" onclick={remove}>
            <Trash2 size={16} />
          </button>
        {/if}
        <button class="notes-save flex h-9 items-center gap-2 rounded-md px-3 text-sm font-medium transition disabled:opacity-50" disabled={busy || !dirty} type="button" onclick={save}>
          <Save size={15} />
          Save
        </button>
      </div>
    </div>

    {#if error}<p role="alert" class="notes-error px-4 py-2">{error}</p>{/if}
    {#if !showingNote}
      <div class="grid flex-1 place-items-center text-sm text-[var(--notes-muted)]">Select a note</div>
    {:else if !editing}
      <article class="min-h-0 flex-1 overflow-auto p-6">
        <div class="mx-auto max-w-3xl">
          <h1 class="mb-3 break-words text-2xl font-semibold">{title || "Untitled"}</h1>
          <div class="mb-6 flex flex-wrap gap-2">{#each tagList as tag}<span class="note-tag">{tag}</span>{/each}</div>
          <div class="markdown note-document break-words">{@html renderMarkdown(body)}</div>
        </div>
      </article>
    {:else}
    <div class="flex min-h-0 flex-1 flex-col gap-3 p-4">
      <input
        class="notes-field h-11 rounded-md px-3 text-lg font-semibold outline-none"
        bind:value={title}
        disabled={busy}
        aria-label="Title"
        placeholder="Title"
      />
      <div class="flex flex-wrap items-center gap-2">
        {#each tagList as tag}<span class="note-tag">{tag}<button title={`Remove ${tag}`} onclick={() => tags = tagList.filter((value) => value !== tag).join(", ")}><X size={12} /></button></span>{/each}
        <input class="notes-field h-8 min-w-0 rounded-md px-2 text-sm" aria-label="New tag" placeholder="Add tag" bind:value={tagInput} onblur={addTag} onkeydown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTag(); } }} />
      </div>
      {#key selected?.id}<NoteEditor value={body} disabled={busy} onchange={(value) => body = value} />{/key}
    </div>
    {/if}
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
    display: flex;
    flex-direction: column;
    background: var(--notes-panel-bg);
    border-right: 1px solid var(--notes-border);
  }

  .notes-editor {
    background: var(--color-surface);
  }

  .notes-toolbar {
    flex-shrink: 0;
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
  .notes-toolbar { flex-shrink: 0; }
  .notes-back { display: none; }
  .note-tag { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; border-radius: 4px; background: var(--color-accent-soft); font-size: 12px; }
  @media (max-width: 700px) {
    .notes-shell { grid-template-columns: minmax(0, 1fr); }
    .notes-editor { display: none; }
    .showing-note .notes-sidebar { display: none; }
    .showing-note .notes-editor { display: flex; }
    .notes-back { display: grid; place-items: center; width: 32px; height: 32px; }
  }
</style>
