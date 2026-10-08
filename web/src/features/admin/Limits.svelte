<script lang="ts">
  import { onMount } from "svelte";
  import { RefreshCw, Save } from "lucide-svelte";
  import { settingsApi, type RuntimeLimits } from "./settingsApi";

  const TG_BOT_API_DOWNLOAD_LIMIT_MB = 20;

  let form = $state<RuntimeLimits>({
    web_user: { active: 2, hourly: 0 },
    tg_guest: { active: 3, hourly: 0 },
    tg_guest_max_upload_mb: TG_BOT_API_DOWNLOAD_LIMIT_MB,
  });
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");

  async function load() {
    loading = true; error = ""; notice = "";
    try { form = await settingsApi<RuntimeLimits>("/limits"); }
    catch (exc) { error = (exc as Error).message; }
    finally { loading = false; }
  }
  async function save(event: SubmitEvent) {
    event.preventDefault(); if (busy) return;
    busy = true; error = ""; notice = "";
    try { form = await settingsApi<RuntimeLimits>("/limits", "PUT", form); notice = "Saved. Applies to new turns within seconds."; }
    catch (exc) { error = (exc as Error).message; }
    finally { busy = false; }
  }
  onMount(() => { void load(); });
</script>

<section class="limits">
  <div class="toolbar">
    <p class="hint">0 turns the limit off. Administrators are never limited.</p>
    <button class="icon" title="Reload" disabled={busy || loading} onclick={load}><RefreshCw size={16} /></button>
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if loading}<p class="empty">Loading…</p>
  {:else}
    <form onsubmit={save}>
      <fieldset>
        <legend>Web users</legend>
        <label>Concurrent turns<input type="number" min="0" required bind:value={form.web_user.active} /></label>
        <label>Turns per hour<input type="number" min="0" required bind:value={form.web_user.hourly} /></label>
      </fieldset>
      <fieldset>
        <legend>Telegram guests</legend>
        <label>Concurrent turns<input type="number" min="0" required bind:value={form.tg_guest.active} /></label>
        <label>Turns per hour<input type="number" min="0" required bind:value={form.tg_guest.hourly} /></label>
        <label>Max attachment, MB<input type="number" min="0" max={TG_BOT_API_DOWNLOAD_LIMIT_MB} required bind:value={form.tg_guest_max_upload_mb} /><small>Telegram Bot API caps downloads at {TG_BOT_API_DOWNLOAD_LIMIT_MB} MB.</small></label>
      </fieldset>
      <footer><button class="primary" type="submit" disabled={busy}><Save size={16} />Save</button></footer>
    </form>
  {/if}
</section>

<style>
  .toolbar, footer { display:flex; align-items:center; gap:10px; }
  .toolbar { justify-content:space-between; margin-bottom:20px; } .hint { font-size:13px; color:var(--color-text-muted); }
  form { display:flex; flex-direction:column; gap:24px; max-width:520px; }
  fieldset { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:14px; border:1px solid var(--color-border); border-radius:8px; padding:16px; }
  legend { padding:0 6px; font-size:12px; font-weight:500; color:var(--color-text-muted); text-transform:uppercase; letter-spacing:.04em; }
  label { display:flex; flex-direction:column; gap:7px; font-size:13px; }
  input { width:100%; min-width:0; border:1px solid var(--color-border); border-radius:6px; background:var(--color-surface); color:var(--color-text); padding:9px 10px; font-size:13px; }
  small { font-size:11px; color:var(--color-text-muted); }
  button { display:inline-flex; align-items:center; justify-content:center; gap:7px; min-height:34px; padding:6px 10px; border-radius:6px; font-size:13px; cursor:pointer; } button:hover { background:var(--color-accent-soft); } button:disabled { opacity:.5; cursor:default; }
  .icon { width:34px; padding:0; flex-shrink:0; } .primary { background:var(--color-accent-soft); color:var(--color-accent); }
  .error, .notice { margin:12px 0; font-size:13px; } .error { color:var(--color-danger); } .notice { color:var(--color-accent); } .empty { padding:40px; text-align:center; color:var(--color-text-muted); }
  footer { justify-content:flex-end; }
  button:focus-visible, input:focus-visible { outline:2px solid var(--color-accent); outline-offset:2px; }
</style>
