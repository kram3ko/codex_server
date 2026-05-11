<script lang="ts">
  import { RefreshCw, Sparkles } from "lucide-svelte";
  import { onMount } from "svelte";

  import type { CodexUsage, UsageWindow } from "../../gen/codex/v1/chat_pb";
  import { chatClient } from "../../shared/lib/clients";

  let usage = $state<CodexUsage | null>(null);
  let loading = $state(false);
  let pulseKey = $state(0);
  let loadedAt = $state<Date | null>(null);

  async function load() {
    loading = true;
    try {
      usage = await chatClient.getCodexUsage({});
      loadedAt = new Date();
      pulseKey += 1;
    } catch {
      usage = null;
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function formatReset(w: UsageWindow): string {
    if (!w.resetsAt) return "—";
    return formatUtc(new Date(Number(w.resetsAt.seconds) * 1000), true);
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

  function formatUtc(d: Date, withWeekday = false): string {
    const pad = (n: number) => String(n).padStart(2, "0");
    const base = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
    return withWeekday ? `${WEEKDAYS[d.getUTCDay()]} ${base}` : base;
  }
</script>

<div class="relative overflow-hidden border-t border-[var(--color-border)] bg-gradient-to-br from-[oklch(22%_0.012_250/0.7)] to-[oklch(18%_0.014_280/0.5)] px-3.5 py-3 backdrop-blur">
  <div class="mb-2.5 flex items-center justify-between">
    <div class="flex items-center gap-1.5">
      <Sparkles size={12} class="text-[var(--color-accent)]" />
      <span class="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text)]">
        {usage?.planType || "Codex"}
      </span>
    </div>
    <button
      class="grid size-7 place-items-center rounded-md text-[var(--color-text-muted)] transition hover:bg-[oklch(96%_0.01_100/0.08)] hover:text-[var(--color-accent)] active:scale-90"
      title="Refresh usage"
      type="button"
      onclick={load}
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
              <span class="font-medium text-[var(--color-text)]">{labelFor(w.windowMinutes)}</span>
              <span class="flex items-baseline gap-1.5 tabular-nums">
                <span class="font-semibold text-[var(--color-text)]">{w.usedPercent.toFixed(0)}%</span>
                <span class="text-[10.5px] text-[var(--color-text-muted)]">· reset {formatReset(w)}</span>
              </span>
            </div>
            <div class="relative h-1.5 overflow-hidden rounded-full bg-[oklch(96%_0.01_100/0.08)]">
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
      <div class="h-1.5 animate-pulse rounded-full bg-[oklch(96%_0.01_100/0.08)]"></div>
      <div class="h-1.5 animate-pulse rounded-full bg-[oklch(96%_0.01_100/0.08)]"></div>
    </div>
  {:else}
    <div class="text-[11px] text-[var(--color-text-muted)]">No data</div>
  {/if}

  {#if loadedAt}
    <div class="mt-2.5 text-[10px] text-[var(--color-text-muted)]">
      updated {formatUtc(loadedAt)}
    </div>
  {/if}
</div>

<style>
  .bar-fill {
    transition: width 0.6s cubic-bezier(0.22, 1, 0.36, 1), background 0.4s ease;
    box-shadow: 0 0 8px oklch(72% 0.18 175 / 0.35);
  }
</style>
