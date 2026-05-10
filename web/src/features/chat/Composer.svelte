<script lang="ts">
  import { Send, Square } from "lucide-svelte";

  let {
    busy,
    onsend,
    oninterrupt
  }: {
    busy: boolean;
    onsend: (text: string) => Promise<void>;
    oninterrupt: () => Promise<void>;
  } = $props();

  let text = $state("");

  async function send() {
    const value = text.trim();
    if (!value || busy) {
      return;
    }
    text = "";
    await onsend(value);
  }

  function keydown(event: KeyboardEvent) {
    // Enter — send. Shift+Enter — newline (default textarea behavior).
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }
</script>

<form
  class="border-t border-[var(--color-border)] bg-[var(--color-surface)]/60 p-3 backdrop-blur"
  onsubmit={(e) => {
    e.preventDefault();
    void send();
  }}
>
  <div class="mx-auto flex max-w-5xl items-end gap-2">
    <div class="glow-ring flex-1 rounded-xl">
      <textarea
        class="max-h-48 min-h-[3.25rem] w-full resize-y rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 py-3 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-transparent"
        bind:value={text}
        disabled={busy}
        onkeydown={keydown}
        placeholder="Message Codex — Enter to send, Shift+Enter for newline"
      ></textarea>
    </div>
    {#if busy}
      <button
        class="grid size-[3.25rem] place-items-center rounded-xl border border-[oklch(70%_0.18_25/0.45)] bg-[oklch(70%_0.18_25/0.12)] text-[var(--color-danger)] transition hover:bg-[oklch(70%_0.18_25/0.2)]"
        title="Interrupt"
        type="button"
        onclick={oninterrupt}
      >
        <Square size={18} />
      </button>
    {:else}
      <button
        class="grid size-[3.25rem] place-items-center rounded-xl bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.3)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
        disabled={!text.trim()}
        title="Send (Enter)"
        type="submit"
      >
        <Send size={18} />
      </button>
    {/if}
  </div>
</form>
