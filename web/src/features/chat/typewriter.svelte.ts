// Adaptive typewriter — chars escape buffer at speed scaled to backlog.
// Empty buffer ≈ 12ms/char (smooth typing feel), drops to 1ms/char once the
// backlog is ~14 chars past ACCELERATE_AT (catches up with fast streams).
// Caller pushes deltas, reads `displayed`.
const MIN_DELAY_MS = 1;
const MAX_DELAY_MS = 12;
const ACCELERATE_AT = 24;

export function createTypewriter() {
  let buffer = "";
  let displayed = $state("");
  let typing = $state(false);
  let resolveDone: (() => void) | null = null;
  let generation = 0;

  async function loop(loopGeneration: number) {
    if (typing) return;
    typing = true;
    while (loopGeneration === generation && buffer.length > 0) {
      const overflow = Math.max(0, buffer.length - ACCELERATE_AT);
      const delay = Math.max(MIN_DELAY_MS, MAX_DELAY_MS - overflow * 0.8);
      displayed += buffer[0];
      buffer = buffer.slice(1);
      await new Promise((r) => setTimeout(r, delay));
    }
    if (loopGeneration === generation) {
      typing = false;
      resolveDone?.();
      resolveDone = null;
    }
  }

  function push(delta: string) {
    if (!delta) return;
    buffer += delta;
    void loop(generation);
  }

  function reset() {
    generation += 1;
    buffer = "";
    displayed = "";
    typing = false;
    resolveDone?.();
    resolveDone = null;
  }

  function flush() {
    if (!buffer) return;
    displayed += buffer;
    buffer = "";
    resolveDone?.();
    resolveDone = null;
  }

  /** Resolve when buffer drained — useful before committing on `done`. */
  function drained(): Promise<void> {
    if (buffer.length === 0 && !typing) return Promise.resolve();
    return new Promise((r) => (resolveDone = r));
  }

  return {
    get displayed() {
      return displayed;
    },
    get fullText() {
      return displayed + buffer;
    },
    get typing() {
      return typing;
    },
    push,
    flush,
    reset,
    drained
  };
}

export type Typewriter = ReturnType<typeof createTypewriter>;
