<script lang="ts">
  import { RefreshCw, Sparkles } from "lucide-svelte";
  import { onDestroy, onMount } from "svelte";

  import type { CodexUsage, UsageWindow } from "../../gen/codex/v1/chat_pb";
  import { chatClient } from "../../shared/lib/clients";
  import ModelPicker from "./ModelPicker.svelte";

  let usage = $state<CodexUsage | null>(null);
  let loading = $state(false);
  let pulseKey = $state(0);
  let loadedAt = $state<Date | null>(null);
  let abort: AbortController | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let destroyed = false;

  // Server-pushed event-driven stream: bootstrap frame з cache (instant),
  // далі pub/sub push на кожен `thread/tokenUsage/updated` від sidecar.
  // Auto-reconnect з exponential backoff (1s → 30s cap) — стрім може
  // обірватися (JWT expired, HMR cycle, server restart); без reconnect-у
  // UI завмер би до full page reload.
  async function consumeStream(attempt = 0) {
    if (destroyed) return;
    abort?.abort();
    abort = new AbortController();
    loading = true;
    try {
      for await (const snapshot of chatClient.streamCodexUsage(
        {},
        { signal: abort.signal }
      )) {
        usage = snapshot;
        loadedAt = new Date();
        pulseKey += 1;
        loading = false;
        attempt = 0; // первий валідний frame → reset backoff
      }
    } catch (exc) {
      if ((exc as { name?: string })?.name === "AbortError") return;
    }
    loading = false;
    if (destroyed) return;
    // Backoff з ±20% jitter — без нього N вкладок після server-restart
    // reconnect-нуться одночасно і дадуть spike навантаження.
    const base = Math.min(30_000, 1_000 * 2 ** attempt);
    const delay = base * (0.8 + Math.random() * 0.4);
    reconnectTimer = setTimeout(() => consumeStream(attempt + 1), delay);
  }

  onMount(() => void consumeStream());
  onDestroy(() => {
    destroyed = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    abort?.abort();
  });

  // Server-side force-refresh: fetch у sidecar + publish у pub/sub. Stream
  // нашого вікна теж отримає snapshot через канал, але unary response повертає
  // його одразу — швидший visual feedback ніж round-trip через pub/sub.
  async function refresh() {
    loading = true;
    try {
      const snapshot = await chatClient.refreshCodexUsage({});
      usage = snapshot;
      loadedAt = new Date();
      pulseKey += 1;
    } finally {
      loading = false;
    }
  }

  function formatReset(w: UsageWindow): string {
    if (!w.resetsAt) return "—";
    return formatLocal(new Date(Number(w.resetsAt.seconds) * 1000), true);
  }

  function barGradient(percent: number): string {
    // 0–60% cyan, 60–85% amber, 85–100% red — typical traffic-light cue.
    if (percent < 60) {
      return "linear-gradient(90deg, oklch(72% 0.18 175), oklch(68% 0.18 200))";
    }
    if (percent < 85) {
      return "linear-gradient(90deg, oklch(72% 0.18 175), oklch(78% 0.17 70))";
    }
    return "linear-gradient(90deg, oklch(78% 0.17 70), oklch(64% 0.22 25))";
  }

  function labelFor(minutes: number): string {
    if (minutes <= 60 * 6) return "5h";
    if (minutes <= 60 * 24 * 8) return "weekly";
    if (minutes >= 60) {
      const h = Math.round(minutes / 60);
      return h % 24 === 0 ? `${h / 24}d` : `${h}h`;
    }
    return `${minutes}m`;
  }

  const windows = $derived(
    usage ? [usage.primary, usage.secondary].filter((w): w is UsageWindow => !!w) : []
  );

  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function formatLocal(d: Date, withWeekday = false): string {
    const pad = (n: number) => String(n).padStart(2, "0");
    const base = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return withWeekday ? `${WEEKDAYS[d.getDay()]} ${base}` : base;
  }
</script>

<div class="usage-panel relative overflow-hidden px-3.5 py-3">
  <div class="mb-3 border-b border-[color-mix(in_oklch,var(--color-border)_50%,transparent)] pb-3">
    <ModelPicker />
  </div>
  <div class="mb-2.5 flex items-center justify-between">
    <div class="flex items-center gap-1.5">
      <Sparkles size={12} class="text-[var(--color-accent)]" />
      <span class="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--usage-text)]">
        {usage?.planType || "Codex"}
      </span>
    </div>
    <button
      class="usage-button grid size-7 place-items-center rounded-md transition active:scale-90"
      title="Refresh usage"
      type="button"
      onclick={refresh}
    >
      <RefreshCw size={13} class={loading ? "animate-spin" : ""} />
    </button>
  </div>

  {#if windows.length}
    {#key pulseKey}
      <div class="space-y-2">
        {#each windows as w (w.windowMinutes)}
          <div class="space-y-1">
            <div class="flex items-baseline justify-between text-[11.5px]">
              <span class="font-medium text-[var(--usage-text)]">{labelFor(w.windowMinutes)}</span>
              <span class="flex items-baseline gap-1.5 tabular-nums">
                <span class="font-semibold text-[var(--usage-text)]">{w.usedPercent.toFixed(0)}%</span>
                <span class="text-[10.5px] text-[var(--usage-muted)]">· reset {formatReset(w)}</span>
              </span>
            </div>
            <div class="usage-track relative h-1.5 overflow-hidden rounded-full">
              <div
                class="h-full rounded-full bar-fill"
                style="width: {Math.min(100, w.usedPercent)}%; background: {barGradient(w.usedPercent)};"
              ></div>
            </div>
          </div>
        {/each}
      </div>
    {/key}
  {:else if loading}
    <div class="space-y-2">
      <div class="usage-track h-1.5 animate-pulse rounded-full"></div>
      <div class="usage-track h-1.5 animate-pulse rounded-full"></div>
    </div>
  {:else}
    <div class="text-[11px] text-[var(--usage-muted)]">No data</div>
  {/if}

  {#if loadedAt}
    <div class="mt-2.5 text-[10px] text-[var(--usage-muted)]">
      updated {formatLocal(loadedAt)}
    </div>
  {/if}
</div>

<style>
  .usage-panel {
    --usage-text: color-mix(in oklch, var(--color-text) 84%, transparent);
    --usage-muted: color-mix(in oklch, var(--color-text) 62%, transparent);
    --usage-panel-bg: color-mix(in oklch, var(--color-surface) 42%, transparent);
    --usage-track-bg: color-mix(in oklch, var(--color-border) 42%, transparent);

    color: var(--usage-text);
    background: var(--usage-panel-bg);
    border-top: 1px solid color-mix(in oklch, var(--color-border) 64%, transparent);
    backdrop-filter: blur(12px) saturate(125%);
    -webkit-backdrop-filter: blur(12px) saturate(125%);
  }

  :global(:root[data-theme="light"]) .usage-panel {
    --usage-panel-bg: color-mix(in oklch, var(--color-surface) 72%, transparent);
    --usage-muted: color-mix(in oklch, var(--color-text) 58%, white 16%);
  }

  .usage-button {
    color: var(--usage-muted);
  }
  .usage-button:hover {
    color: var(--color-accent);
    background: color-mix(in oklch, var(--color-surface-2) 54%, transparent);
  }

  .usage-track {
    background: var(--usage-track-bg);
  }

  .bar-fill {
    transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1), background 0.4s ease;
    box-shadow: 0 0 8px oklch(72% 0.18 175 / 0.35);
  }
</style>
