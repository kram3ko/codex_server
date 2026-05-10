// Adaptive typewriter — chars escape buffer at speed scaled to backlog.
// Empty buffer ≈ 22ms/char (smooth typing feel), overflow > 200 ≈ 2ms/char
// (catches up with fast streams). Caller pushes deltas, reads `displayed`.
const MIN_DELAY_MS = 2;
const MAX_DELAY_MS = 22;
const ACCELERATE_AT = 50;

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
      const delay = Math.max(MIN_DELAY_MS, MAX_DELAY_MS - overflow * 0.4);
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

  /** Resolve when buffer drained — useful before committing on `done`. */
  function drained(): Promise<void> {
    if (buffer.length === 0 && !typing) return Promise.resolve();
    return new Promise((r) => (resolveDone = r));
  }

  return {
    get displayed() {
      return displayed;
    },
    get typing() {
      return typing;
    },
    push,
    reset,
    drained
  };
}

export type Typewriter = ReturnType<typeof createTypewriter>;
