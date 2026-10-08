<script lang="ts">
  import { LogOut, MessageSquareText, Monitor, Moon, NotebookTabs, Settings, Sparkles, Sun } from "lucide-svelte";

  import AdminPanel from "./features/admin/AdminPanel.svelte";
  import { auth } from "./features/auth/auth";
  import Login from "./features/auth/Login.svelte";
  import Signup from "./features/auth/Signup.svelte";
  import Chat from "./features/chat/Chat.svelte";
  import Notes from "./features/notes/Notes.svelte";
  import { userClient } from "./shared/lib/clients";
  import { theme } from "./shared/lib/theme.svelte";

  type Route = "chat" | "notes" | "admin";
  type AuthView = "login" | "signup";

  // URL `?invite=XYZ` → одразу signup-форма з префілленим токеном.
  const inviteFromUrl = new URLSearchParams(window.location.search).get("invite") ?? "";

  let signedIn = $state(auth.signedIn);
  let route = $state<Route>("chat");
  let authView = $state<AuthView>(inviteFromUrl ? "signup" : "login");
  let isAdmin = $state(false);
  let notesView: Notes | undefined = $state();

  function navigate(next: Route) {
    if (route === "notes" && !notesView?.canNavigate()) return false;
    route = next;
    return true;
  }

  async function refreshRole() {
    try {
      const me = await userClient.me({});
      isAdmin = me.role === "ADMIN";
    } catch {
      isAdmin = false;
    }
  }

  $effect(() => {
    const onLoginEvent = () => {
      signedIn = true;
      void refreshRole();
    };
    const onLogoutEvent = () => {
      signedIn = false;
      isAdmin = false;
    };
    window.addEventListener("auth:login", onLoginEvent);
    window.addEventListener("auth:logout", onLogoutEvent);
    if (signedIn) void refreshRole();
    return () => {
      window.removeEventListener("auth:login", onLoginEvent);
      window.removeEventListener("auth:logout", onLogoutEvent);
    };
  });

  function onLogin() {
    signedIn = true;
    route = "chat";
    if (inviteFromUrl) {
      // Чистимо `?invite=...` з URL після успішного signup, щоб f5 не подразнював.
      const url = new URL(window.location.href);
      url.searchParams.delete("invite");
      window.history.replaceState({}, "", url.toString());
    }
    void refreshRole();
  }

  function logout() {
    if (route === "notes" && !notesView?.canNavigate()) return;
    void auth.logout();
    signedIn = false;
    isAdmin = false;
    authView = "login";
  }
</script>

{#if !signedIn}
  {#if authView === "signup"}
    <Signup
      onlogin={onLogin}
      onswitch={() => (authView = "login")}
      initialInvite={inviteFromUrl}
    />
  {:else}
    <Login
      onlogin={onLogin}
      onswitch={() => (authView = "signup")}
    />
  {/if}
{:else}
  <div class="app-shell min-h-screen text-[var(--color-text)]">
    {#snippet navigation(compact = false)}
    <div class="console-navigation sidebar" class:compact>
    <nav class="app-nav" aria-label="Main navigation">
      <button title="Chat" aria-label="Chat" class:current={route === "chat"} onclick={() => navigate("chat")}><MessageSquareText size={16} /><span>Chat</span></button>
      <button title="Notes" aria-label="Notes" class:current={route === "notes"} onclick={() => navigate("notes")}><NotebookTabs size={16} /><span>Notes</span></button>
      {#if isAdmin}<button class:current={route === "admin"} title="Settings" aria-label="Settings" onclick={() => navigate("admin")}><Settings size={16} /><span>Settings</span></button>{/if}
    </nav>
    <header class="console-brandbar flex items-center justify-between gap-2">
      <div class="console-brand flex items-center gap-2.5">
        <div class="grid size-9 place-items-center rounded-lg bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-sm font-semibold text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.25)]">
          <Sparkles size={16} strokeWidth={2.5} />
        </div>
        <div class="brand-label">
          <div class="text-sm font-semibold leading-none tracking-tight">Codex</div>
          <div class="text-[11px] text-[var(--color-text-muted)]">personal console</div>
        </div>
      </div>

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
    </div>
    {/snippet}

    {#snippet workspaceContent()}
    {#if route === "notes"}
      <Notes bind:this={notesView} />
    {:else if route === "admin" && isAdmin}
      <AdminPanel />
    {/if}
    {/snippet}
    <Chat {navigation} content={route === "chat" ? undefined : workspaceContent} onopenchat={() => navigate("chat")} />
  </div>
{/if}

<style>
  .console-navigation { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:8px 12px; height:100%; border-bottom:1px solid var(--color-border); background:var(--color-surface); }
  .console-brandbar { order:-1; flex:1; }
  .app-nav { display:flex; gap:4px; }
  .app-nav button { display:flex; align-items:center; justify-content:center; gap:5px; height:32px; padding:0 8px; border-radius:6px; color:var(--color-text-muted); font-size:12px; }
  .app-nav button:hover,.app-nav button.current { color:var(--color-accent); background:var(--color-accent-soft); }
  .sidebar { flex-direction:column; align-items:stretch; height:auto; gap:8px; }
  .sidebar .app-nav button { flex:1; }
  .compact { padding:8px 4px; }
  .compact .console-brandbar,.compact .console-brandbar > div:last-child,.compact .app-nav { flex-direction:column; }
  .compact .brand-label,.compact .app-nav :global(span) { display:none; }
  .compact .app-nav button { flex:none; }
  @media(max-width:700px) { .console-navigation { gap:6px; padding:8px; } .compact { padding:8px 4px; } }
</style>
