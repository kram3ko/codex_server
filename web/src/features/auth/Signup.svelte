<script lang="ts">
  import { untrack } from "svelte";
  import { Sparkles } from "lucide-svelte";

  import { auth } from "./auth";

  let {
    onlogin,
    onswitch,
    initialInvite = "",
  }: {
    onlogin: () => void;
    onswitch: () => void;
    initialInvite?: string;
  } = $props();

  let email = $state("");
  let password = $state("");
  let displayName = $state("");
  // initialInvite — одноразовий seed (з URL `?invite=...`); після того юзер
  // може редагувати поле. `untrack` каже Svelte: read once, не реагуй на зміни.
  let inviteToken = $state(untrack(() => initialInvite));
  let error = $state("");
  let busy = $state(false);

  async function submit() {
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      error = "Email and password are required";
      return;
    }
    if (password.length < 8) {
      error = "Password must be at least 8 characters";
      return;
    }
    busy = true;
    error = "";
    try {
      await auth.register(trimmedEmail, password, displayName.trim(), inviteToken.trim());
      onlogin();
    } catch (exc) {
      error = exc instanceof Error ? exc.message : "Signup failed";
    } finally {
      busy = false;
    }
  }
</script>

<main class="grid min-h-screen place-items-center px-4">
  <section class="glass w-full max-w-sm rounded-2xl p-6 shadow-2xl shadow-[oklch(0%_0_0/0.4)]">
    <div class="mb-6 flex items-center gap-3">
      <div class="grid size-11 place-items-center rounded-xl bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.35)]">
        <Sparkles size={20} strokeWidth={2.5} />
      </div>
      <div>
        <h1 class="text-lg font-semibold tracking-tight">Create account</h1>
        <p class="text-sm text-[var(--color-text-muted)]">Sign up with an invite</p>
      </div>
    </div>

    <form
      class="space-y-3"
      onsubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div class="glow-ring rounded-lg">
        <input
          class="h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-transparent"
          bind:value={email}
          autocomplete="email"
          placeholder="Email"
          type="email"
        />
      </div>
      <div class="glow-ring rounded-lg">
        <input
          class="h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-transparent"
          bind:value={password}
          autocomplete="new-password"
          placeholder="Password (≥ 8 chars)"
          type="password"
        />
      </div>
      <div class="glow-ring rounded-lg">
        <input
          class="h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-transparent"
          bind:value={displayName}
          autocomplete="name"
          placeholder="Display name (optional)"
          type="text"
        />
      </div>
      <div class="glow-ring rounded-lg">
        <input
          class="h-11 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-transparent"
          bind:value={inviteToken}
          placeholder="Invite token"
          type="text"
          autocomplete="off"
        />
      </div>
      {#if error}
        <p class="text-sm text-[var(--color-danger)]">{error}</p>
      {/if}
      <button
        class="h-11 w-full rounded-lg bg-gradient-to-r from-[oklch(72%_0.18_175)] to-[oklch(70%_0.16_230)] px-4 font-medium text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.25)] transition hover:brightness-110 disabled:opacity-50 disabled:hover:brightness-100"
        disabled={busy}
        type="submit"
      >
        {busy ? "Creating account…" : "Sign up"}
      </button>
      <p class="text-center text-sm text-[var(--color-text-muted)]">
        Already have an account?
        <button
          type="button"
          class="text-[var(--color-accent)] hover:underline"
          onclick={onswitch}
        >
          Sign in
        </button>
      </p>
    </form>
  </section>
</main>
