<script lang="ts">
  import { Copy, Globe, KeyRound, Plus, Send, Trash2, Users } from "lucide-svelte";
  import { onMount } from "svelte";

  import { adminClient } from "../../shared/lib/clients";
  import type { AdminUser, Invite } from "../../gen/codex/v1/admin_pb";
  import { UserRolePb } from "../../gen/codex/v1/admin_pb";

  let { onopenkeys }: { onopenkeys: () => void } = $props();

  let invites = $state<Invite[]>([]);
  let users = $state<AdminUser[]>([]);
  let loading = $state(false);
  let error = $state("");
  let busy = $state(false);
  let copiedToken = $state<string | null>(null);

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
      setTimeout(() => {
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

  function roleLabel(role: UserRolePb): string {
    if (role === UserRolePb.USER_ROLE_ADMIN) return "admin";
    if (role === UserRolePb.USER_ROLE_USER) return "user";
    return "?";
  }

  onMount(() => {
    void refresh();
  });
</script>

<main class="mx-auto max-w-4xl px-4 py-6">
  <header class="mb-6 flex items-center justify-between">
    <div>
      <h1 class="text-xl font-semibold tracking-tight">Admin</h1>
      <p class="text-sm text-[var(--color-text-muted)]">Invites + users</p>
    </div>
    <div class="flex items-center gap-2">
      <button
        class="flex h-9 items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm font-medium text-[var(--color-text-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
        onclick={onopenkeys}
        type="button"
      >
        <KeyRound size={15} />
        Keys & API
      </button>
      <button
        class="flex h-9 items-center gap-2 rounded-lg bg-gradient-to-r from-[oklch(72%_0.18_175)] to-[oklch(70%_0.16_230)] px-3 text-sm font-medium text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.25)] transition hover:brightness-110 disabled:opacity-50"
        disabled={busy}
        onclick={createInvite}
        type="button"
      >
        <Plus size={15} />
        {busy ? "Creating…" : "New invite"}
      </button>
    </div>
  </header>

  {#if error}
    <div class="mb-4 rounded-lg border border-[var(--color-danger)] bg-[oklch(40%_0.15_25/0.15)] px-3 py-2 text-sm text-[var(--color-danger)]">
      {error}
    </div>
  {/if}

  <section class="glass mb-6 rounded-2xl p-4">
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
      <ul class="space-y-2">
        {#each invites as invite (invite.id)}
          <li class="flex items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <code class="flex-1 truncate font-mono text-xs text-[var(--color-text-muted)]">
              {inviteUrl(invite.token)}
            </code>
            <span class="text-xs text-[var(--color-text-muted)]">
              expires {formatDate(invite.expiresAt?.seconds)}
            </span>
            <button
              class="grid size-8 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-accent)]"
              onclick={() => copyLink(invite.token)}
              title="Copy link"
              type="button"
            >
              <Copy size={14} />
            </button>
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
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="glass rounded-2xl p-4">
    <h2 class="mb-3 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
      <Users size={14} />
      Users ({users.length})
    </h2>
    {#if users.length === 0}
      <p class="text-sm text-[var(--color-text-muted)]">No users yet.</p>
    {:else}
      <ul class="space-y-1.5">
        {#each users as u (u.id)}
          <li class="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm">
            <div class="flex flex-col">
              <span class="font-medium">{u.displayName || u.email || `tg:${u.tgUserId}`}</span>
              {#if u.email}
                <span class="text-xs text-[var(--color-text-muted)]">{u.email}</span>
              {/if}
            </div>
            <div class="flex items-center gap-1.5">
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
          </li>
        {/each}
      </ul>
    {/if}
  </section>
</main>
