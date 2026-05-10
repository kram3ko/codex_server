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

<main class="grid h-[calc(100vh-3.5rem)] min-h-0 grid-cols-[320px_1fr]">
  <aside class="min-h-0 border-r border-[#d9d3c8] bg-[#fbfaf7]">
    <div class="flex h-12 items-center gap-2 border-b border-[#d9d3c8] px-3">
      <div class="relative flex-1">
        <Search class="absolute left-2 top-2.5 text-[#87908c]" size={15} />
        <input
          class="h-9 w-full rounded-md border border-[#cfc7ba] bg-white pl-8 pr-2 text-sm outline-none focus:border-[#357960]"
          bind:value={query}
          onkeydown={(event) => event.key === "Enter" && load()}
        />
      </div>
      <button class="grid size-9 place-items-center rounded-md hover:bg-[#f1eee8]" type="button" title="New note" onclick={fresh}>
        <Plus size={16} />
      </button>
    </div>

    {#if error}
      <div class="border-b border-[#e7c9c1] bg-[#fff5f2] px-3 py-2 text-sm text-[#a33a2b]">{error}</div>
    {/if}

    <div class="min-h-0 overflow-y-auto p-2">
      {#each notes as note (note.id.toString())}
        <button
          class="mb-1 w-full rounded-md px-3 py-2 text-left hover:bg-[#f1eee8] {selected?.id === note.id ? 'bg-[#e6f0eb]' : ''}"
          type="button"
          onclick={() => edit(note)}
        >
          <div class="truncate text-sm font-medium">{note.title}</div>
          <div class="mt-1 text-xs text-[#60706a]">{formatTime(note.updatedAt)}</div>
        </button>
      {/each}
    </div>
  </aside>

  <section class="flex min-h-0 flex-col bg-[#f6f4ef]">
    <div class="flex h-12 items-center justify-between border-b border-[#d9d3c8] bg-[#fbfaf7] px-4">
      <h2 class="text-sm font-semibold">{selected ? "Edit note" : "New note"}</h2>
      <div class="flex gap-2">
        {#if selected}
          <button class="grid size-9 place-items-center rounded-md border border-[#e7c9c1] bg-white text-[#a33a2b] hover:bg-[#fff5f2]" type="button" title="Delete" onclick={remove}>
            <Trash2 size={16} />
          </button>
        {/if}
        <button class="flex h-9 items-center gap-2 rounded-md bg-[#1f6b55] px-3 text-sm font-medium text-white hover:bg-[#185643] disabled:opacity-50" disabled={busy} type="button" onclick={save}>
          <Save size={15} />
          Save
        </button>
      </div>
    </div>

    <div class="grid min-h-0 flex-1 grid-rows-[auto_auto_1fr] gap-3 p-4">
      <input
        class="h-11 rounded-md border border-[#cfc7ba] bg-white px-3 text-lg font-semibold outline-none focus:border-[#357960]"
        bind:value={title}
        placeholder="Title"
      />
      <input
        class="h-10 rounded-md border border-[#cfc7ba] bg-white px-3 text-sm outline-none focus:border-[#357960]"
        bind:value={tags}
        placeholder="tags, comma-separated"
      />
      <textarea
        class="min-h-0 resize-none rounded-md border border-[#cfc7ba] bg-white p-3 leading-7 outline-none focus:border-[#357960]"
        bind:value={body}
        placeholder="Note body"
      ></textarea>
    </div>
  </section>
</main>
