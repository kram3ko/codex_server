<script lang="ts">
  import { Copy, Globe, Plus, Send, Trash2, Users } from "lucide-svelte";
  import { onMount, onDestroy } from "svelte";

  import { adminClient } from "../../shared/lib/clients";
  import type { AdminUser, Invite } from "../../gen/codex/v1/admin_pb";
  import { UserRolePb } from "../../gen/codex/v1/admin_pb";
  import Integrations from "./Integrations.svelte";
  import Limits from "./Limits.svelte";
  import { settingsApi, type UserProfile } from "./settingsApi";


  let invites = $state<Invite[]>([]);
  let users = $state<AdminUser[]>([]);
  let loading = $state(false);
  let error = $state("");
  let busy = $state(false);
  let copiedToken = $state<string | null>(null);
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  onDestroy(() => clearTimeout(copyTimer));
  let tab = $state<"users" | "invites" | "integrations" | "ssh" | "limits">("integrations");
  let usernames = $state<Record<string, string | null>>({});
  let lastActive = $state<Record<string, string | null>>({});
  let query = $state("");
  let roleFilter = $state("");
  const visibleUsers = $derived(users.filter((user) =>
    (!roleFilter || roleLabel(user.role) === roleFilter) &&
    [user.displayName, user.email, usernames[user.id.toString()], user.tgUserId?.toString(), user.id.toString()]
      .some((value) => value?.toLowerCase().includes(query.toLowerCase()))
  ));

  const baseUrl = `${window.location.protocol}//${window.location.host}`;

  function inviteUrl(token: string): string {
    return `${baseUrl}/?invite=${token}`;
  }

  async function refresh() {
    loading = true;
    error = "";
    try {
      const [inviteResp, userResp] = await Promise.all([
        adminClient.listInvites({ includeUsed: false }),
        adminClient.listUsers({})
      ]);
      invites = inviteResp.invites;
      users = userResp.users;
      const profiles = await settingsApi<UserProfile[]>("/users");
      usernames = Object.fromEntries(profiles.map((row) => [row.id, row.tg_username]));
      lastActive = Object.fromEntries(profiles.map((row) => [row.id, row.last_active_at]));
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to load admin data";
    } finally {
      loading = false;
    }
  }

  async function createInvite() {
    busy = true;
    error = "";
    try {
      const resp = await adminClient.createInvite({ ttlDays: 7 });
      if (resp.invite) {
        invites = [resp.invite, ...invites];
      }
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Create invite failed";
    } finally {
      busy = false;
    }
  }

  async function revoke(id: bigint) {
    if (!confirm("Revoke this invite? Anyone holding the link won't be able to use it.")) return;
    try {
      await adminClient.revokeInvite({ inviteId: id });
      invites = invites.filter((i) => i.id !== id);
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Revoke failed";
    }
  }

  async function copyLink(token: string) {
    try {
      await navigator.clipboard.writeText(inviteUrl(token));
      copiedToken = token;
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => {
        if (copiedToken === token) copiedToken = null;
      }, 1500);
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Copy failed";
    }
  }

  function formatDate(seconds: bigint | undefined): string {
    if (!seconds) return "—";
    return new Date(Number(seconds) * 1000).toLocaleString();
  }

  function formatIso(value: string | null | undefined): string {
    return value ? new Date(value).toLocaleString() : "—";
  }

  function roleLabel(role: UserRolePb): string {
    if (role === UserRolePb.USER_ROLE_ADMIN) return "admin";
    if (role === UserRolePb.USER_ROLE_USER) return "user";
    return "?";
  }

  onMount(() => {
    void refresh();
  });
</script>

<main class="settings-shell px-4 py-6"><div class="settings-inner">
  <header class="mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-xl font-semibold">Settings</h1>
      <p class="text-sm text-[var(--color-text-muted)]">Administration</p>
    </div>
    <div class="flex items-center gap-2">
      {#if tab === "invites"}<button
        class="flex h-9 items-center gap-2 rounded-lg bg-gradient-to-r from-[oklch(72%_0.18_175)] to-[oklch(70%_0.16_230)] px-3 text-sm font-medium text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.25)] transition hover:brightness-110 disabled:opacity-50"
        disabled={busy}
        onclick={createInvite}
        type="button"
      >
        <Plus size={15} />
        {busy ? "Creating…" : "New invite"}
      </button>
      {/if}
    </div>
  </header>

  {#if error}
    <div class="mb-4 rounded-lg border border-[var(--color-danger)] bg-[oklch(40%_0.15_25/0.15)] px-3 py-2 text-sm text-[var(--color-danger)]">
      {error}
    </div>
  {/if}

  <nav class="mb-5 flex gap-2 border-b border-[var(--color-border)] pb-3" aria-label="Settings sections">
    <button class="rounded-md px-3 py-2 text-sm" class:active={tab === "integrations"} onclick={() => tab = "integrations"}>Integrations</button>
    <button class="rounded-md px-3 py-2 text-sm" class:active={tab === "ssh"} onclick={() => tab = "ssh"}>SSH keys</button>
    <button class="rounded-md px-3 py-2 text-sm" class:active={tab === "limits"} onclick={() => tab = "limits"}>Limits</button>
    <button class="rounded-md px-3 py-2 text-sm" class:active={tab === "users"} onclick={() => tab = "users"}>Users ({users.length})</button>
    <button class="rounded-md px-3 py-2 text-sm" class:active={tab === "invites"} onclick={() => tab = "invites"}>Invitations ({invites.length})</button>
  </nav>
  {#if tab === "integrations" || tab === "ssh"}
    {#key tab}<Integrations sshOnly={tab === "ssh"} />{/key}
  {:else if tab === "limits"}
    <Limits />
  {:else if tab === "invites"}
  <section class="mb-6">
    <h2 class="mb-3 text-sm font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
      Active invites ({invites.length})
    </h2>
    {#if loading}
      <p class="text-sm text-[var(--color-text-muted)]">Loading…</p>
    {:else if invites.length === 0}
      <p class="text-sm text-[var(--color-text-muted)]">
        No active invites. Click <span class="font-medium">New invite</span> to issue one.
      </p>
    {:else}
      <div class="overflow-x-auto"><table class="w-full text-left text-sm"><thead><tr><th>Invitation link</th><th>Expires</th><th>Actions</th></tr></thead><tbody>
        {#each invites as invite (invite.id)}
          <tr>
            <td><input class="w-full min-w-48 bg-transparent font-mono text-xs" aria-label="Invitation link" readonly value={inviteUrl(invite.token)} onclick={(event) => event.currentTarget.select()} /></td>
            <td class="text-xs text-[var(--color-text-muted)]">{formatDate(invite.expiresAt?.seconds)}</td>
            <td><div class="flex items-center gap-2">
            {#if navigator.clipboard}<button
              class="grid size-8 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-accent)]"
              onclick={() => copyLink(invite.token)}
              title="Copy link"
              type="button"
            >
              <Copy size={14} />
            </button>{/if}
            <button
              class="grid size-8 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-danger)]"
              onclick={() => revoke(invite.id)}
              title="Revoke"
              type="button"
            >
              <Trash2 size={14} />
            </button>
            {#if copiedToken === invite.token}
              <span class="text-xs text-[var(--color-accent)]">copied</span>
            {/if}
            </div></td>
          </tr>
        {/each}
      </tbody></table></div>
    {/if}
  </section>
  {:else}
  <section>
    <div class="mb-4 flex flex-wrap gap-3">
      <input class="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm" aria-label="Search users" placeholder="Name, email or ID" bind:value={query} />
      <select class="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm" aria-label="Role" bind:value={roleFilter}><option value="">All roles</option><option value="admin">Admin</option><option value="user">User</option></select>
    </div>
    <h2 class="mb-3 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
      <Users size={14} />
      Users ({users.length})
    </h2>
    {#if visibleUsers.length === 0}
      <p class="text-sm text-[var(--color-text-muted)]">No users yet.</p>
    {:else}
      <div class="overflow-x-auto"><table class="w-full text-left text-sm">
        <thead><tr><th>Name</th><th>Telegram</th><th>Telegram ID</th><th>Registered</th><th>Last activity</th><th>Access</th></tr></thead>
        <tbody>
        {#each visibleUsers as u (u.id)}
          <tr>
            <td>
              <span class="font-medium">{u.displayName || u.email || `tg:${u.tgUserId}`}</span>
              {#if u.email}
                <span class="block text-xs text-[var(--color-text-muted)]">{u.email}</span>
              {/if}
            </td>
            <td>{#if usernames[u.id.toString()]}<a class="text-[var(--color-accent)] hover:underline" href={`https://t.me/${encodeURIComponent(usernames[u.id.toString()] ?? "")}`} target="_blank" rel="noopener noreferrer">@{usernames[u.id.toString()]}</a>{:else}—{/if}</td>
            <td class="font-mono">{u.tgUserId?.toString() ?? "—"}</td>
            <td>{formatDate(u.createdAt?.seconds)}</td>
            <td>{formatIso(lastActive[u.id.toString()])}</td>
            <td><div class="flex items-center gap-1.5">
              {#if u.email}
                <span class="inline-flex h-6 w-6 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-text-muted)]" title="Web account">
                  <Globe size={12} />
                </span>
              {/if}
              {#if u.tgUserId}
                <span class="inline-flex h-6 w-6 items-center justify-center rounded-md border border-[oklch(70%_0.16_230/0.4)] bg-[oklch(70%_0.16_230/0.12)] text-[oklch(70%_0.16_230)]" title="Telegram (id: {u.tgUserId})">
                  <Send size={12} />
                </span>
              {/if}
              <span class="inline-flex h-6 w-14 items-center justify-center rounded-md bg-[var(--color-accent-soft)] text-xs font-medium text-[var(--color-accent)]">
                {roleLabel(u.role)}
              </span>
            </div>
            </td>
          </tr>
        {/each}
        </tbody></table></div>
    {/if}
  </section>
  {/if}
</div></main>

<style>
  .settings-shell { min-height:100%; background:var(--color-surface); }
  .settings-inner { max-width:1280px; margin:auto; }
  .active { background: var(--color-accent-soft); color: var(--color-accent); }
  nav { flex-wrap: wrap; }
  th, td { padding: 12px 10px; border-bottom: 1px solid var(--color-border); }
  th { font-size: 12px; color: var(--color-text-muted); font-weight: 500; white-space: nowrap; }
  td { max-width: 300px; overflow-wrap: anywhere; }
  tbody tr:hover { background: var(--color-accent-soft); }
</style>
