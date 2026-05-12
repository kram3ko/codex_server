// Shared signal for cross-component reactions on turn lifecycle. Currently
// used by Usage.svelte to auto-refresh codex rate-limits after every done.
// `$state` rune доступний у `.svelte.ts` файлах через svelte-preprocessor.

export const turnSignal = $state({ doneCount: 0 });
