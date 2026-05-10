<script lang="ts">
  import { LogOut, MessageSquareText, Monitor, Moon, NotebookTabs, Sparkles, Sun } from "lucide-svelte";

  import { auth } from "./features/auth/auth";
  import Login from "./features/auth/Login.svelte";
  import Chat from "./features/chat/Chat.svelte";
  import Notes from "./features/notes/Notes.svelte";
  import { theme } from "./shared/lib/theme.svelte";

  type Route = "chat" | "notes";

  let signedIn = $state(auth.signedIn);
  let route = $state<Route>("chat");

  function onLogin() {
    signedIn = true;
    route = "chat";
  }

  function logout() {
    auth.logout();
    signedIn = false;
  }
</script>

{#if !signedIn}
  <Login onlogin={onLogin} />
{:else}
  <div class="min-h-screen text-[var(--color-text)]">
    <header class="glass sticky top-0 z-20 flex h-14 items-center justify-between px-4">
      <div class="flex items-center gap-2.5">
        <div class="grid size-9 place-items-center rounded-lg bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-sm font-semibold text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.25)]">
          <Sparkles size={16} strokeWidth={2.5} />
        </div>
        <div>
          <div class="text-sm font-semibold leading-none tracking-tight">Codex</div>
          <div class="text-[11px] text-[var(--color-text-muted)]">personal console</div>
        </div>
      </div>

      <nav class="glass-soft flex items-center gap-1 rounded-lg p-1">
        <button
          class="flex h-8 items-center gap-2 rounded-md px-3 text-sm transition-colors {route === 'chat' ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]' : 'text-[var(--color-text-muted)] hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-text)]'}"
          type="button"
          title="Chat"
          onclick={() => (route = "chat")}
        >
          <MessageSquareText size={15} />
          Chat
        </button>
        <button
          class="flex h-8 items-center gap-2 rounded-md px-3 text-sm transition-colors {route === 'notes' ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]' : 'text-[var(--color-text-muted)] hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-text)]'}"
          type="button"
          title="Notes"
          onclick={() => (route = "notes")}
        >
          <NotebookTabs size={15} />
          Notes
        </button>
      </nav>

      <div class="flex items-center gap-2">
        <button
          class="grid size-9 place-items-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
          type="button"
          title="Theme: {theme.mode} ({theme.resolved})"
          onclick={theme.cycle}
        >
          {#if theme.mode === "auto"}
            <Monitor size={16} />
          {:else if theme.mode === "light"}
            <Sun size={16} />
          {:else}
            <Moon size={16} />
          {/if}
        </button>
        <button
          class="grid size-9 place-items-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
          type="button"
          title="Logout"
          onclick={logout}
        >
          <LogOut size={16} />
        </button>
      </div>
    </header>

    {#if route === "chat"}
      <Chat />
    {:else}
      <Notes />
    {/if}
  </div>
{/if}
