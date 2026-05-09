// Theme manager: light / dark / auto. Persists to localStorage, applies
// `data-theme` attribute on <html>, listens for OS scheme changes when auto.
const KEY = "codex.theme";
type Mode = "light" | "dark" | "auto";

function readStored(): Mode {
  if (typeof localStorage === "undefined") return "auto";
  const raw = localStorage.getItem(KEY);
  return raw === "light" || raw === "dark" || raw === "auto" ? raw : "auto";
}

function systemPrefers(): "light" | "dark" {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function apply(resolved: "light" | "dark") {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
}

function createTheme() {
  let mode = $state<Mode>(readStored());
  let system = $state<"light" | "dark">(systemPrefers());

  function compute(): "light" | "dark" {
    return mode === "auto" ? system : mode;
  }

  apply(compute());

  if (typeof window !== "undefined") {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", (e) => {
      system = e.matches ? "light" : "dark";
      apply(compute());
    });
  }

  function set(next: Mode) {
    mode = next;
    if (typeof localStorage !== "undefined") localStorage.setItem(KEY, next);
    apply(compute());
  }

  function cycle() {
    set(mode === "light" ? "dark" : mode === "dark" ? "auto" : "light");
  }

  return {
    get mode() {
      return mode;
    },
    get resolved() {
      return compute();
    },
    set,
    cycle
  };
}

export const theme = createTheme();
