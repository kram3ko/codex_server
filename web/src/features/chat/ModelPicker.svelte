<script lang="ts">
  import { ChevronDown, Cpu } from "lucide-svelte";
  import { onMount } from "svelte";

  import type { CodexModel } from "../../gen/codex/v1/codex_pb";
  import { codexClient } from "../../shared/lib/clients";

  const DEFAULT = "";
  const SAVE_DEBOUNCE_MS = 400;
  const SAVED_FLASH_MS = 1500;
  const EXPANDED_KEY = "modelPickerExpanded";

  let models = $state<CodexModel[]>([]);
  let fallbackEffort = $state("");
  let modelPref = $state(DEFAULT);
  let effortPref = $state(DEFAULT);
  let loading = $state(true);
  let expanded = $state(localStorage.getItem(EXPANDED_KEY) === "1");
  let status = $state<"idle" | "saving" | "saved" | "error">("idle");
  let errorText = $state("");
  let saveTimer: ReturnType<typeof setTimeout> | null = null;
  // Saves run strictly one after another and read state at execution time,
  // so a slow earlier request can never overwrite a newer choice.
  let saveChain: Promise<void> = Promise.resolve();
  let flashTimer: ReturnType<typeof setTimeout> | null = null;

  const catalogDefault = $derived(models.find((m) => m.isDefault) ?? models[0] ?? null);
  const activeModel = $derived(
    modelPref === DEFAULT ? catalogDefault : (models.find((m) => m.id === modelPref) ?? catalogDefault)
  );
  const efforts = $derived(activeModel?.supportedReasoningEfforts ?? []);
  const defaultEffort = $derived(
    efforts.some((e) => e.value === fallbackEffort)
      ? fallbackEffort
      : (activeModel?.defaultReasoningEffort ?? "")
  );
  const effortIsDefault = $derived(effortPref === DEFAULT);
  const shownEffort = $derived(effortIsDefault ? defaultEffort : effortPref);
  const sliderIndex = $derived(Math.max(0, efforts.findIndex((e) => e.value === shownEffort)));
  const sliderPercent = $derived(
    efforts.length > 1 ? (sliderIndex / (efforts.length - 1)) * 100 : 0
  );
  const shownDescription = $derived(efforts[sliderIndex]?.description ?? "");

  onMount(async () => {
    try {
      const [catalog, prefs] = await Promise.all([
        codexClient.listModels({}),
        codexClient.getPreferences({})
      ]);
      models = catalog.models;
      fallbackEffort = catalog.fallbackReasoningEffort;
      modelPref = prefs.model ?? DEFAULT;
      effortPref = prefs.reasoningEffort ?? DEFAULT;
    } catch (exc) {
      status = "error";
      errorText = (exc as Error).message;
    } finally {
      loading = false;
    }
  });

  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveChain = saveChain.then(save);
    }, SAVE_DEBOUNCE_MS);
  }

  async function save() {
    status = "saving";
    errorText = "";
    try {
      await codexClient.updatePreferences({
        model: modelPref === DEFAULT ? undefined : modelPref,
        reasoningEffort: effortPref === DEFAULT ? undefined : effortPref
      });
      status = "saved";
      if (flashTimer) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => (status = "idle"), SAVED_FLASH_MS);
    } catch (exc) {
      status = "error";
      errorText = (exc as Error).message;
    }
  }

  function onModelChange(event: Event) {
    modelPref = (event.currentTarget as HTMLSelectElement).value;
    // Effort is validated per model: reset to default when switching models
    // so a level unsupported by the new model never gets persisted.
    if (!efforts.some((e) => e.value === effortPref)) effortPref = DEFAULT;
    scheduleSave();
  }

  function onSlider(event: Event) {
    const idx = Number((event.currentTarget as HTMLInputElement).value);
    const picked = efforts[idx];
    if (!picked) return;
    effortPref = picked.value;
    scheduleSave();
  }

  function toggleExpanded() {
    expanded = !expanded;
    localStorage.setItem(EXPANDED_KEY, expanded ? "1" : "0");
  }

  function resetEffort() {
    effortPref = DEFAULT;
    scheduleSave();
  }
</script>

<div class="picker space-y-2.5">
  <button
    type="button"
    class="header flex w-full items-center justify-between gap-2 text-left"
    onclick={toggleExpanded}
    aria-expanded={expanded}
  >
    <span class="flex min-w-0 items-center gap-1.5">
      <Cpu size={12} class="shrink-0 text-[var(--color-accent)]" />
      {#if expanded}
        <span class="text-[11px] font-semibold uppercase tracking-[0.08em]">Model</span>
      {:else if loading}
        <span class="picker-track h-3 w-24 animate-pulse rounded"></span>
      {:else}
        <span class="truncate text-[11.5px] font-medium">{activeModel?.displayName ?? "—"}</span>
        <span class="text-[11px] text-[var(--picker-muted)]">·</span>
        <span class="text-[11px] tabular-nums text-[var(--picker-muted)]">{shownEffort || "—"}</span>
        {#if modelPref === DEFAULT && effortIsDefault}
          <span class="chip chip-on">default</span>
        {/if}
      {/if}
    </span>
    <span class="flex shrink-0 items-center gap-1.5">
      <span class="status text-[10px]" data-status={status}>
        {#if status === "saving"}saving…{:else if status === "saved"}saved{:else if status === "error"}error{/if}
      </span>
      <ChevronDown size={13} class="chevron {expanded ? 'open' : ''}" />
    </span>
  </button>

  {#if expanded}
    {#if loading}
      <div class="picker-track h-7 animate-pulse rounded-md"></div>
    {:else if !models.length}
      <div class="text-[11px] text-[var(--picker-muted)]">No models</div>
    {:else}
      <select class="picker-select w-full rounded-md px-2 py-1.5 text-[12px]" value={modelPref} onchange={onModelChange}>
        <option value={DEFAULT}>Default · {catalogDefault?.displayName ?? "—"}</option>
        {#each models as m (m.id)}
          <option value={m.id}>{m.displayName}</option>
        {/each}
      </select>

      {#if efforts.length}
        <div class="space-y-1.5">
          <div class="flex items-baseline justify-between text-[11.5px]">
            <span class="font-medium">Reasoning</span>
            <span class="flex items-baseline gap-1.5">
              <span class="font-semibold tabular-nums">{shownEffort || "—"}</span>
              {#if effortIsDefault}
                <span class="chip chip-on">default</span>
              {:else}
                <button type="button" class="chip chip-off" onclick={resetEffort} title="Back to default">
                  reset
                </button>
              {/if}
            </span>
          </div>
          <input
            class="slider w-full"
            type="range"
            min="0"
            max={efforts.length - 1}
            step="1"
            value={sliderIndex}
            style="--fill: {sliderPercent}%"
            oninput={onSlider}
            aria-label="Reasoning effort"
          />
          <div class="flex justify-between text-[9.5px] uppercase tracking-[0.06em] text-[var(--picker-muted)]">
            {#each efforts as e (e.value)}
              <span class:active={e.value === shownEffort} class="tick">
                {e.value}{e.value === defaultEffort ? "*" : ""}
              </span>
            {/each}
          </div>
          {#if shownDescription}
            <div class="text-[10px] leading-snug text-[var(--picker-muted)]">{shownDescription}</div>
          {/if}
          <div class="text-[10px] text-[var(--picker-muted)]">* default for this model</div>
        </div>
      {/if}
    {/if}

    {#if errorText}
      <div class="text-[10px] text-[var(--color-danger)]">{errorText}</div>
    {/if}
  {/if}
</div>

<style>
  .picker {
    --picker-muted: color-mix(in oklch, var(--color-text) 62%, transparent);
    --picker-track-bg: color-mix(in oklch, var(--color-border) 42%, transparent);
  }

  .picker-track {
    background: var(--picker-track-bg);
  }

  .header {
    color: inherit;
    cursor: pointer;
  }
  .header:hover :global(.chevron) {
    color: var(--color-accent);
  }
  :global(.chevron) {
    color: var(--picker-muted);
    transition: transform 0.2s ease, color 0.15s ease;
  }
  :global(.chevron.open) {
    transform: rotate(180deg);
  }

  .picker-select {
    color: inherit;
    background: color-mix(in oklch, var(--color-surface-2) 60%, transparent);
    border: 1px solid color-mix(in oklch, var(--color-border) 70%, transparent);
    outline: none;
  }
  .picker-select:focus {
    border-color: var(--color-accent);
  }

  .chip {
    font-size: 9.5px;
    line-height: 1;
    padding: 3px 6px;
    border-radius: 999px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .chip-on {
    color: var(--color-accent);
    background: var(--color-accent-soft);
  }
  .chip-off {
    color: var(--picker-muted);
    background: var(--picker-track-bg);
    transition: color 0.15s ease;
  }
  .chip-off:hover {
    color: var(--color-accent);
  }

  .slider {
    -webkit-appearance: none;
    appearance: none;
    height: 6px;
    border-radius: 999px;
    background: linear-gradient(
      90deg,
      var(--color-accent) 0%,
      var(--color-accent) var(--fill),
      var(--picker-track-bg) var(--fill),
      var(--picker-track-bg) 100%
    );
    outline: none;
    cursor: pointer;
  }
  .slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--color-surface);
    border: 2px solid var(--color-accent);
    box-shadow: 0 0 8px oklch(72% 0.18 175 / 0.45);
    transition: transform 0.15s ease;
  }
  .slider::-webkit-slider-thumb:hover {
    transform: scale(1.15);
  }
  .slider::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--color-surface);
    border: 2px solid var(--color-accent);
    box-shadow: 0 0 8px oklch(72% 0.18 175 / 0.45);
  }

  .tick {
    transition: color 0.2s ease;
  }
  .tick.active {
    color: var(--color-accent);
    font-weight: 600;
  }

  .status {
    color: var(--picker-muted);
    transition: opacity 0.2s ease;
  }
  .status[data-status="saved"] {
    color: var(--color-accent);
  }
  .status[data-status="error"] {
    color: var(--color-danger);
  }
</style>
