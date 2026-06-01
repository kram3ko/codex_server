<script lang="ts">
  import { ArrowLeft, KeyRound, Plus, Ticket, Trash2 } from "lucide-svelte";
  import { onMount } from "svelte";

  import { adminClient } from "../../shared/lib/clients";
  import type { ApiToken, SshKey } from "../../gen/codex/v1/admin_pb";

  let { onback }: { onback: () => void } = $props();

  let sshKeys = $state<SshKey[]>([]);
  let apiTokens = $state<ApiToken[]>([]);
  let loading = $state(false);
  let error = $state("");

  let sshName = $state("");
  let sshHost = $state("");
  let sshUser = $state("");
  let sshPrivateKey = $state("");
  let addingSsh = $state(false);

  let tokName = $state("");
  let tokHost = $state("");
  let tokUser = $state("");
  let tokToken = $state("");
  let addingTok = $state(false);

  async function refresh() {
    loading = true;
    error = "";
    try {
      const [keys, tokens] = await Promise.all([
        adminClient.listSshKeys({}),
        adminClient.listApiTokens({})
      ]);
      sshKeys = keys.keys;
      apiTokens = tokens.tokens;
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Failed to load keys";
    } finally {
      loading = false;
    }
  }

  async function addSshKey(e: Event) {
    e.preventDefault();
    addingSsh = true;
    error = "";
    try {
      const key = await adminClient.addSshKey({
        name: sshName.trim(),
        host: sshHost.trim(),
        user: sshUser.trim(),
        privateKey: sshPrivateKey
      });
      sshKeys = [...sshKeys.filter((k) => k.name !== key.name), key].sort((a, b) =>
        a.name.localeCompare(b.name)
      );
      sshName = "";
      sshHost = "";
      sshUser = "";
      sshPrivateKey = "";
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Add SSH key failed";
    } finally {
      addingSsh = false;
    }
  }

  async function deleteSshKey(name: string) {
    if (!confirm(`Delete SSH key "${name}"? The agent loses access to that host.`)) return;
    try {
      await adminClient.deleteSshKey({ name });
      sshKeys = sshKeys.filter((k) => k.name !== name);
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Delete SSH key failed";
    }
  }

  async function addApiToken(e: Event) {
    e.preventDefault();
    addingTok = true;
    error = "";
    try {
      const tok = await adminClient.addApiToken({
        name: tokName.trim(),
        host: tokHost.trim(),
        user: tokUser.trim(),
        token: tokToken
      });
      apiTokens = [...apiTokens.filter((t) => t.name !== tok.name), tok].sort((a, b) =>
        a.name.localeCompare(b.name)
      );
      tokName = "";
      tokHost = "";
      tokUser = "";
      tokToken = "";
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Add API token failed";
    } finally {
      addingTok = false;
    }
  }

  async function deleteApiToken(name: string) {
    if (!confirm(`Delete API token "${name}"? The agent loses HTTPS access to that host.`)) return;
    try {
      await adminClient.deleteApiToken({ name });
      apiTokens = apiTokens.filter((t) => t.name !== name);
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Delete API token failed";
    }
  }

  onMount(() => void refresh());
</script>

<main class="mx-auto max-w-4xl px-4 py-6">
  <header class="mb-6 flex items-center gap-3">
    <button
      class="grid size-9 shrink-0 place-items-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
      onclick={onback}
      title="Back to Admin"
      type="button"
    >
      <ArrowLeft size={16} />
    </button>
    <div>
      <h1 class="text-xl font-semibold tracking-tight">Keys &amp; API</h1>
      <p class="text-sm text-[var(--color-text-muted)]">SSH keys for the codex agent — added live, no restart</p>
    </div>
  </header>

  {#if error}
    <div class="mb-4 rounded-lg border border-[var(--color-danger)] bg-[oklch(40%_0.15_25/0.15)] px-3 py-2 text-sm text-[var(--color-danger)]">
      {error}
    </div>
  {/if}

  <section class="glass rounded-2xl p-4">
    <h2 class="mb-3 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
      <KeyRound size={14} />
      SSH keys ({sshKeys.length})
    </h2>

    {#if loading}
      <p class="mb-4 text-sm text-[var(--color-text-muted)]">Loading…</p>
    {:else if sshKeys.length === 0}
      <p class="mb-4 text-sm text-[var(--color-text-muted)]">
        No keys yet. Add one below — the agent clones <code class="font-mono text-xs">git@&lt;name&gt;:org/repo</code> using it.
      </p>
    {:else}
      <ul class="mb-4 space-y-2">
        {#each sshKeys as k (k.name)}
          <li class="flex items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <span class="font-mono text-sm font-semibold text-[var(--color-accent)]">{k.name}</span>
            <span class="text-xs text-[var(--color-text-muted)]">{k.user}@{k.host}</span>
            <code class="ml-auto truncate font-mono text-xs text-[var(--color-text-muted)]">git@{k.name}:org/repo.git</code>
            <button
              class="grid size-8 shrink-0 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-danger)]"
              onclick={() => deleteSshKey(k.name)}
              title="Delete key"
              type="button"
            >
              <Trash2 size={14} />
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    <form class="grid gap-2" onsubmit={addSshKey}>
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input
          bind:value={sshName}
          class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
          placeholder="name (github-salesdep)"
          required
        />
        <input
          bind:value={sshHost}
          class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
          placeholder="host (github.com)"
          required
        />
        <input
          bind:value={sshUser}
          class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
          placeholder="user (git)"
        />
      </div>
      <textarea
        bind:value={sshPrivateKey}
        class="resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-xs text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
        placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;…&#10;-----END OPENSSH PRIVATE KEY-----"
        rows={4}
        required
      ></textarea>
      <button
        class="flex h-9 items-center justify-center gap-2 self-start rounded-lg bg-[var(--color-accent-soft)] px-4 text-sm font-medium text-[var(--color-accent)] transition hover:brightness-110 disabled:opacity-50"
        disabled={addingSsh}
        type="submit"
      >
        <Plus size={15} />
        {addingSsh ? "Adding…" : "Add key"}
      </button>
    </form>
  </section>

  <section class="glass mt-6 rounded-2xl p-4">
    <h2 class="mb-3 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
      <Ticket size={14} />
      API tokens ({apiTokens.length})
    </h2>

    {#if apiTokens.length === 0}
      <p class="mb-4 text-sm text-[var(--color-text-muted)]">
        No tokens yet. Add one — the agent clones <code class="font-mono text-xs">https://&lt;host&gt;/org/repo</code> over HTTPS.
      </p>
    {:else}
      <ul class="mb-4 space-y-2">
        {#each apiTokens as t (t.name)}
          <li class="flex items-center gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2">
            <span class="font-mono text-sm font-semibold text-[var(--color-accent)]">{t.name}</span>
            <span class="text-xs text-[var(--color-text-muted)]">{t.user}@{t.host}</span>
            <code class="ml-auto truncate font-mono text-xs text-[var(--color-text-muted)]">https://{t.host}/org/repo.git</code>
            <button
              class="grid size-8 shrink-0 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.06)] hover:text-[var(--color-danger)]"
              onclick={() => deleteApiToken(t.name)}
              title="Delete token"
              type="button"
            >
              <Trash2 size={14} />
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    <form class="grid gap-2" onsubmit={addApiToken}>
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input
          bind:value={tokName}
          class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
          placeholder="name (gh-main)"
          required
        />
        <input
          bind:value={tokHost}
          class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
          placeholder="host (github.com)"
          required
        />
        <input
          bind:value={tokUser}
          class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
          placeholder="user (x-access-token / oauth2)"
        />
      </div>
      <input
        bind:value={tokToken}
        class="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-xs text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent)]"
        placeholder="token (ghp_… / glpat-…)"
        type="password"
        required
      />
      <button
        class="flex h-9 items-center justify-center gap-2 self-start rounded-lg bg-[var(--color-accent-soft)] px-4 text-sm font-medium text-[var(--color-accent)] transition hover:brightness-110 disabled:opacity-50"
        disabled={addingTok}
        type="submit"
      >
        <Plus size={15} />
        {addingTok ? "Adding…" : "Add token"}
      </button>
    </form>
  </section>
</main>
