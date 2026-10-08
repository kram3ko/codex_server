<script lang="ts">
  import { onMount } from "svelte";
  import { Check, KeyRound, LockKeyhole, Pencil, Plus, RefreshCw, Search, Trash2, X } from "lucide-svelte";
  import { settingsApi, type Integration, type IntegrationKind, type TelegramChat } from "./settingsApi";
  let { sshOnly = false }: { sshOnly?: boolean } = $props();
  let entries = $state<Integration[]>([]);
  let chats = $state<TelegramChat[]>([]);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let notice = $state("");
  let query = $state("");
  let editing = $state(false);
  let original = $state<Integration | null>(null);
  let name = $state("");
  let kind = $state<IntegrationKind>("github");
  let host = $state("github.com");
  let username = $state("");
  let secret = $state("");
  let enabled = $state(true);
  let replaceSecret = $state(false);
  let adminIds = $state("");
  let webhookUrl = $state("");
  let groupQuery = $state("");
  let telegramView = $state(false);
  const labels: Record<IntegrationKind, string> = { github: "GitHub", gitlab: "GitLab", ssh: "SSH", telegram: "Telegram" };
  const shown = $derived(entries.filter((row) => (sshOnly ? row.kind === "ssh" : row.kind !== "ssh") && `${row.name} ${row.host} ${row.username}`.toLowerCase().includes(query.toLowerCase())));
  const shownChats = $derived(chats.filter((row) => `${row.title} ${row.chat_id} ${row.bot_id}`.toLowerCase().includes(groupQuery.toLowerCase())));

  async function load() {
    loading = true; error = "";
    try { entries = await settingsApi<Integration[]>("/integrations"); }
    catch (exc) { error = (exc as Error).message; }
    finally { loading = false; }
  }
  async function loadChats() {
    busy = true; error = "";
    try { chats = await settingsApi<TelegramChat[]>("/telegram/chats"); telegramView = true; }
    catch (exc) { error = (exc as Error).message; }
    finally { busy = false; }
  }
  function open(row: Integration | null) {
    original = row; name = row?.name ?? ""; kind = row?.kind ?? (sshOnly ? "ssh" : "github");
    host = row?.host ?? (sshOnly ? "" : "github.com"); username = row?.username ?? "";
    secret = ""; enabled = row?.enabled ?? true; replaceSecret = !row;
    adminIds = row?.telegram.admin_ids.join(", ") ?? ""; webhookUrl = row?.telegram.webhook_url ?? "";
    error = ""; notice = ""; editing = true;
  }
  function changeKind() {
    host = ({ github: "github.com", gitlab: "gitlab.com", ssh: "", telegram: "" })[kind];
    secret = "";
  }
  function close() {
    if (busy) return;
    if (!confirm("Close this form and discard unsaved changes?")) return;
    secret = ""; editing = false;
  }
  async function save(event: SubmitEvent) {
    event.preventDefault(); if (busy) return;
    busy = true; error = "";
    try {
      const ids = adminIds.split(",").map((id) => id.trim()).filter(Boolean).map(Number);
      if (ids.some((id) => !Number.isSafeInteger(id) || id <= 0)) throw new Error("Telegram administrator IDs must be positive integers");
      await settingsApi<Integration>(original ? `/integrations/${original.id}` : "/integrations", original ? "PUT" : "POST", {
        name, kind, host, username, enabled, telegram: { admin_ids: ids, webhook_url: webhookUrl },
        ...(replaceSecret ? { secret } : {})
      });
      secret = ""; editing = false; notice = "Saved. Runtime settings are being applied.";
      await load();
    } catch (exc) { error = (exc as Error).message; }
    finally { busy = false; }
  }
  async function check(row: Integration) {
    busy = true; error = ""; notice = "";
    try { notice = (await settingsApi<{message: string}>(`/integrations/${row.id}/check`, "POST")).message; }
    catch (exc) { error = (exc as Error).message; }
    finally { busy = false; }
  }
  async function remove(row: Integration) {
    if (!confirm(`Delete "${row.name}" and remove its access?`)) return;
    busy = true; error = "";
    try { await settingsApi(`/integrations/${row.id}`, "DELETE"); await load(); }
    catch (exc) { error = (exc as Error).message; }
    finally { busy = false; }
  }
  async function toggleChat(row: TelegramChat) {
    busy = true; error = "";
    try {
      await settingsApi(`/telegram/chats/${row.id}`, "PATCH", { replies_enabled: !row.replies_enabled });
      chats = chats.map((item) => item.id === row.id ? { ...item, replies_enabled: !item.replies_enabled } : item);
    } catch (exc) { error = (exc as Error).message; }
    finally { busy = false; }
  }
  const date = (value: string) => new Date(value).toLocaleString();
  onMount(() => { void load(); });
</script>

<section class="integrations">
  <div class="toolbar">
    <label class="search"><Search size={16} /><input placeholder="Search connections" aria-label="Search connections" bind:value={query} /></label>
    <button class="icon" title="Refresh" disabled={busy || loading} onclick={load}><RefreshCw size={16} /></button>
    <button class="primary" disabled={busy} onclick={() => open(null)}><Plus size={16} />{sshOnly ? "Add SSH key" : "Add integration"}</button>
  </div>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if notice}<p role="status" class="notice">{notice}</p>{/if}
  <div class="table-scroll"><table>
    <thead><tr><th>Name</th><th>Type / host</th><th>Account</th><th>Secret</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead>
    <tbody>{#each shown as row (row.id)}
      <tr><td><button class="name" onclick={() => open(row)}>{row.name}</button></td><td>{labels[row.kind]}<small>{row.host || "Telegram Bot API"}</small></td><td>{row.bot_username ? `@${row.bot_username}` : row.username || "—"}</td>
        <td><span class="secret"><LockKeyhole size={13} />{row.has_secret ? "************" : "Not set"}</span><small>{row.has_secret ? "Encrypted" : ""}</small></td>
        <td><span class="badge" class:enabled={row.enabled}>{row.enabled ? "Enabled" : "Disabled"}</span></td><td>{date(row.updated_at)}</td>
        <td><div class="actions"><button class="icon" title="Edit details" disabled={busy} onclick={() => open(row)}><Pencil size={15} /></button><button class="icon" title="Check connection" disabled={busy} onclick={() => check(row)}><Check size={15} /></button><button class="icon danger" title="Delete" disabled={busy} onclick={() => remove(row)}><Trash2 size={15} /></button></div></td></tr>
    {:else}<tr><td colspan="7" class="empty">{loading ? "Loading..." : "No connections"}</td></tr>{/each}</tbody>
  </table></div>
  {#if !sshOnly}
    <div class="groups-heading"><h2>Telegram groups &amp; chats</h2><button disabled={busy} onclick={loadChats}><RefreshCw size={15} />{telegramView ? "Refresh" : "Show chats"}</button></div>
    {#if telegramView}
      <input class="group-search" aria-label="Search Telegram chats" placeholder="Chat name or Telegram ID" bind:value={groupQuery} />
      <div class="table-scroll"><table><thead><tr><th>Chat</th><th>Telegram ID</th><th>Bot ID</th><th>Membership</th><th>Replies</th><th>Last event</th></tr></thead><tbody>
        {#each shownChats as chat (chat.id)}<tr><td>{chat.title}<small>{chat.kind}</small></td><td class="mono">{chat.chat_id}</td><td class="mono">{chat.bot_id}</td><td>{chat.membership}</td><td><input type="checkbox" aria-label={`Replies in ${chat.title}`} checked={chat.replies_enabled} disabled={busy} onchange={() => toggleChat(chat)} /></td><td>{date(chat.updated_at)}</td></tr>
        {:else}<tr><td colspan="6" class="empty">No known chats</td></tr>{/each}
      </tbody></table></div>
    {/if}
  {/if}
  {#if editing}
    <div class="scrim"></div>
    <aside class="drawer" aria-label="Integration details">
      <header><h2>{original ? original.name : "New connection"}</h2><button class="icon" title="Close" disabled={busy} onclick={close}><X size={18} /></button></header>
      <form onsubmit={save}>
        <label>Type<select bind:value={kind} disabled={!!original || sshOnly} onchange={changeKind}>{#each Object.entries(labels) as [value, label]}<option {value}>{label}</option>{/each}</select></label>
        <label>Name<input required maxlength="100" bind:value={name} /></label>
        {#if kind !== "telegram"}<label>Host<input required bind:value={host} /></label><label>Username<input bind:value={username} /></label>{/if}
        <label class="inline"><input type="checkbox" bind:checked={enabled} />Enabled</label>
        {#if original}<div class="stored"><LockKeyhole size={16} /><span>************<small>Encrypted secret saved</small></span></div><label class="inline"><input type="checkbox" bind:checked={replaceSecret} />Replace secret</label>{/if}
        {#if replaceSecret}<label>{kind === "ssh" ? "Private key" : "Token"}{#if kind === "ssh"}<textarea rows="6" required bind:value={secret} autocomplete="off"></textarea>{:else}<input type="password" required bind:value={secret} autocomplete="new-password" />{/if}</label>{/if}
        {#if kind === "telegram"}
          <label>Administrator Telegram IDs<input bind:value={adminIds} placeholder="123456789, 987654321" /></label>
          <details><summary>Webhook</summary><label>HTTPS webhook URL<input type="url" bind:value={webhookUrl} placeholder="https://example.com/tg/webhook" /></label></details>
        {/if}
        {#if original}<dl>{#if original.fingerprint}<dt>SSH fingerprint</dt><dd class="fingerprint">{original.fingerprint}</dd>{/if}<dt>Created</dt><dd>{date(original.created_at)}</dd><dt>Updated</dt><dd>{date(original.updated_at)}</dd></dl>{/if}
        {#if error}<p role="alert" class="error">{error}</p>{/if}
        <footer><button type="button" disabled={busy} onclick={close}>Cancel</button><button class="primary" disabled={busy}><KeyRound size={15} />{busy ? "Saving..." : "Save"}</button></footer>
      </form>
    </aside>
  {/if}
</section>

<style>
  .toolbar, .actions, header, footer, .groups-heading { display:flex; align-items:center; gap:10px; }
  .toolbar { flex-wrap:wrap; margin-bottom:20px; } .search { flex:1; display:flex; align-items:center; gap:8px; min-width:150px; }
  input:not([type=checkbox]), select, textarea { width:100%; min-width:0; border:1px solid var(--color-border); border-radius:6px; background:var(--color-surface); color:var(--color-text); padding:9px 10px; font-size:13px; }
  input[type=checkbox] { accent-color:var(--color-accent); width:16px; height:16px; }
  button { display:inline-flex; align-items:center; justify-content:center; gap:7px; min-height:34px; padding:6px 10px; border-radius:6px; font-size:13px; cursor:pointer; } button:hover { background:var(--color-accent-soft); } button:disabled { opacity:.5; cursor:default; }
  .icon { width:34px; padding:0; flex-shrink:0; } .primary { background:var(--color-accent-soft); color:var(--color-accent); } .danger,.error { color:var(--color-danger); } .error,.notice { margin:12px 0; font-size:13px; overflow-wrap:anywhere; } .notice { color:var(--color-accent); }
  .table-scroll { overflow:auto; } table { width:100%; text-align:left; border-collapse:collapse; font-size:13px; } th,td { padding:12px 10px; border-bottom:1px solid var(--color-border); } th { white-space:nowrap; color:var(--color-text-muted); font-weight:500; font-size:12px; } td { max-width:240px; overflow-wrap:anywhere; } tbody tr:hover { background:var(--color-accent-soft); } small { display:block; font-size:11px; color:var(--color-text-muted); margin-top:4px; }
  .name { padding:0; text-align:left; font-weight:600; } .secret,.stored { display:flex; align-items:center; gap:8px; } .badge { font-size:11px; border-radius:4px; background:var(--color-surface); padding:3px 6px; } .badge.enabled { color:var(--color-accent); } .empty { padding:40px; text-align:center; color:var(--color-text-muted); } .mono { font-family:monospace; }
  .groups-heading { justify-content:space-between; margin-top:32px; padding-top:20px; border-top:1px solid var(--color-border); } h2 { font-weight:600; font-size:16px; overflow-wrap:anywhere; } .group-search { margin:12px 0; }
  .scrim { position:fixed; inset:0; background:#0005; z-index:39; } .drawer { position:fixed; right:0; top:0; bottom:0; z-index:40; width:min(440px,100%); background:var(--color-surface); border-left:1px solid var(--color-border); padding:24px; overflow-y:auto; }
  header { justify-content:space-between; margin-bottom:24px; } form { display:flex; flex-direction:column; gap:18px; } form label { display:flex; flex-direction:column; gap:7px; font-size:13px; } form label.inline { flex-direction:row; align-items:center; } .stored { border:1px solid var(--color-border); padding:12px; border-radius:6px; } footer { justify-content:flex-end; border-top:1px solid var(--color-border); padding-top:16px; } dt { font-size:11px; color:var(--color-text-muted); } dd { font-size:12px; margin:4px 0 12px; }
  button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline:2px solid var(--color-accent); outline-offset:2px; }
  .fingerprint { overflow-wrap:anywhere; font-family:monospace; }
</style>
