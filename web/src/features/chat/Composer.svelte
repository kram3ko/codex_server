<script lang="ts">
  import { File as FileIcon, Mic, Paperclip, Send, Square, X } from "lucide-svelte";

  import Spinner from "../../shared/components/Spinner.svelte";
  import { uploadsClient } from "../../shared/lib/clients";

  type Pending = {
    key: string;
    file: File;
    previewUrl: string;
    uploadId?: bigint;
    status: "uploading" | "ready" | "failed";
    error?: string;
  };

  let {
    busy,
    onsend,
    oninterrupt
  }: {
    busy: boolean;
    onsend: (text: string, imageIds: bigint[], audioIds: bigint[]) => Promise<void>;
    oninterrupt: () => Promise<void>;
  } = $props();

  let text = $state("");
  let pending = $state<Pending[]>([]);
  let recorder = $state<MediaRecorder | null>(null);
  let recording = $state(false);
  let transcribing = $state(false);
  let audioUploadIds = $state<bigint[]>([]);
  let recordingStartedAt = $state<number | null>(null);
  let recordingElapsed = $state(0);

  $effect(() => {
    if (!recording || !recordingStartedAt) {
      recordingElapsed = 0;
      return;
    }
    const start = recordingStartedAt;
    recordingElapsed = Math.floor((Date.now() - start) / 1000);
    const id = setInterval(() => {
      recordingElapsed = Math.floor((Date.now() - start) / 1000);
    }, 500);
    return () => clearInterval(id);
  });

  function fmtRec(s: number): string {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${m}:${r.toString().padStart(2, "0")}`;
  }
  const uploadingNow = $derived(pending.some((p) => p.status === "uploading"));
  const canSend = $derived(
    !uploadingNow &&
      !transcribing &&
      (text.trim().length > 0 || pending.some((p) => p.status === "ready") || audioUploadIds.length > 0)
  );

  async function send() {
    if (!canSend) return;
    const value = text.trim();
    const imageIds = pending
      .filter((p) => p.status === "ready" && p.uploadId)
      .map((p) => p.uploadId!);
    const audioIds = [...audioUploadIds];
    text = "";
    pending.forEach((p) => URL.revokeObjectURL(p.previewUrl));
    pending = [];
    audioUploadIds = [];
    await onsend(value, imageIds, audioIds);
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send();
    }
  }

  function onpaste(event: ClipboardEvent) {
    const items = event.clipboardData?.items;
    if (!items) return;
    const images = Array.from(items)
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter((f): f is File => f !== null);
    if (!images.length) return;
    event.preventDefault();
    for (const file of images) void attach(file);
  }

  async function attach(file: File) {
    const key = `${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;
    const previewUrl = URL.createObjectURL(file);
    const item: Pending = { key, file, previewUrl, status: "uploading" };
    pending = [...pending, item];
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const response = await uploadsClient.uploadOnce({
        filename: file.name || "pasted.png",
        mime: file.type || "image/png",
        data: bytes
      });
      if (!response.upload) throw new Error("empty upload response");
      pending = pending.map((p) =>
        p.key === key ? { ...p, uploadId: response.upload!.id, status: "ready" } : p
      );
    } catch (exc) {
      pending = pending.map((p) =>
        p.key === key
          ? { ...p, status: "failed", error: exc instanceof Error ? exc.message : "upload failed" }
          : p
      );
    }
  }

  function remove(key: string) {
    const item = pending.find((p) => p.key === key);
    if (item) URL.revokeObjectURL(item.previewUrl);
    pending = pending.filter((p) => p.key !== key);
  }

  let fileInput = $state<HTMLInputElement | null>(null);

  function pickFiles() {
    fileInput?.click();
  }

  async function onFilesPicked(event: Event) {
    const target = event.currentTarget as HTMLInputElement;
    const files = Array.from(target.files ?? []);
    target.value = "";
    for (const file of files) {
      if (file.type.startsWith("image/")) {
        void attach(file);
      } else if (file.type.startsWith("audio/") || file.type.startsWith("video/")) {
        void processAudio(file);
      } else {
        void attachGeneric(file);
      }
    }
  }

  async function attachGeneric(file: File) {
    const key = `${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;
    const item: Pending = { key, file, previewUrl: "", status: "uploading" };
    pending = [...pending, item];
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const response = await uploadsClient.uploadOnce({
        filename: file.name || "file",
        mime: file.type || "application/octet-stream",
        data: bytes
      });
      if (!response.upload) throw new Error("empty upload response");
      pending = pending.map((p) =>
        p.key === key ? { ...p, uploadId: response.upload!.id, status: "ready" } : p
      );
    } catch (exc) {
      pending = pending.map((p) =>
        p.key === key
          ? { ...p, status: "failed", error: exc instanceof Error ? exc.message : "upload failed" }
          : p
      );
    }
  }

  async function toggleMic() {
    if (recording && recorder) {
      recorder.stop();
      return;
    }
    if (recording || transcribing) return;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return;
    }
    const mr = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    mr.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    mr.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      recording = false;
      recorder = null;
      recordingStartedAt = null;
      const blob = new Blob(chunks, { type: mr.mimeType || "audio/webm" });
      await processAudio(blob);
    };
    recorder = mr;
    recording = true;
    recordingStartedAt = Date.now();
    mr.start();
  }

  async function processAudio(blob: Blob) {
    transcribing = true;
    try {
      const mime = (blob.type.split(";")[0] || "audio/webm").trim();
      const ext = mime.split("/")[1] || "webm";
      const bytes = new Uint8Array(await blob.arrayBuffer());
      const upload = await uploadsClient.uploadOnce({
        filename: `voice.${ext}`,
        mime,
        data: bytes
      });
      if (!upload.upload) return;
      let transcript = "";
      try {
        const resp = await uploadsClient.transcribeUpload({ uploadId: upload.upload.id });
        transcript = resp.text.trim();
      } catch {
        /* STT failed — still attach the audio */
      }
      const carriedText = (text + (text && transcript ? " " : "") + transcript).trim();
      // Empty audio (silence/no-speech) without a typed prompt: don't fire an
      // empty turn — server would reject `text=""`. Stage the upload so user
      // can type a prompt and Send normally.
      if (!carriedText) {
        audioUploadIds = [...audioUploadIds, upload.upload.id];
        return;
      }
      // TG-style: voice = self-contained message. Send straight away with
      // transcript as the prompt and the audio attached.
      text = "";
      const stagedImages = pending
        .filter((p) => p.status === "ready" && p.uploadId)
        .map((p) => p.uploadId!);
      pending.forEach((p) => URL.revokeObjectURL(p.previewUrl));
      pending = [];
      const allAudio = [...audioUploadIds, upload.upload.id];
      audioUploadIds = [];
      await onsend(carriedText, stagedImages, allAudio);
    } catch {
      /* upload itself failed */
    } finally {
      transcribing = false;
    }
  }
</script>

<form
  class="border-t border-[var(--color-border)] bg-[var(--color-surface)]/60 p-3 backdrop-blur"
  onsubmit={(e) => {
    e.preventDefault();
    void send();
  }}
>
  <div class="mx-auto flex max-w-5xl flex-col gap-2">
    {#if pending.length}
      <div class="flex flex-wrap gap-2">
        {#each pending as p (p.key)}
          <div class="group relative h-16 w-16 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
            {#if p.previewUrl}
              <img src={p.previewUrl} alt="" class="h-full w-full object-cover {p.status === 'failed' ? 'opacity-40' : ''}" />
            {:else}
              <div class="flex h-full w-full flex-col items-center justify-center gap-1 p-1 text-[var(--color-text-muted)] {p.status === 'failed' ? 'opacity-40' : ''}">
                <FileIcon size={20} />
                <span class="line-clamp-2 text-center text-[8px] leading-tight">{p.file.name}</span>
              </div>
            {/if}
            {#if p.status === "uploading"}
              <div class="absolute inset-0 grid place-items-center bg-[oklch(0%_0_0/0.4)]">
                <Spinner />
              </div>
            {/if}
            {#if p.status === "failed"}
              <div class="absolute inset-x-0 bottom-0 bg-[oklch(60%_0.2_25/0.85)] px-1 py-0.5 text-center text-[10px] text-white" title={p.error}>
                failed
              </div>
            {/if}
            <button
              class="absolute right-0.5 top-0.5 grid size-4 place-items-center rounded-full bg-[oklch(0%_0_0/0.6)] text-white opacity-0 transition group-hover:opacity-100"
              title="Remove"
              type="button"
              onclick={() => remove(p.key)}
            >
              <X size={10} />
            </button>
          </div>
        {/each}
      </div>
    {/if}

    <div class="flex items-end gap-2">
      <input
        bind:this={fileInput}
        class="hidden"
        type="file"
        multiple
        onchange={onFilesPicked}
      />
      <button
        class="grid size-[3.25rem] place-items-center rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] transition hover:text-[var(--color-accent)] hover:border-[var(--color-accent)]/40"
        title="Attach file"
        type="button"
        onclick={pickFiles}
      >
        <Paperclip size={18} />
      </button>
      <button
        class="grid size-[3.25rem] place-items-center rounded-xl border border-[var(--color-border)] transition {recording
          ? 'bg-[oklch(70%_0.18_25/0.15)] text-[var(--color-danger)] animate-pulse'
          : transcribing
            ? 'bg-[var(--color-surface)] text-[var(--color-text-muted)]'
            : 'bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:text-[var(--color-accent)] hover:border-[var(--color-accent)]/40'}"
        disabled={transcribing}
        title={recording ? "Stop recording" : transcribing ? "Transcribing…" : "Voice input"}
        type="button"
        onclick={toggleMic}
      >
        {#if transcribing}
          <Spinner />
        {:else}
          <Mic size={18} />
        {/if}
      </button>
      <div class="glow-ring flex-1 rounded-xl">
        <textarea
          class="max-h-48 min-h-[3.25rem] w-full resize-y rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3.5 py-3 text-base leading-7 text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)] focus:border-transparent"
          bind:value={text}
          onkeydown={keydown}
          {onpaste}
          placeholder="Message Codex — Enter to send, Shift+Enter for newline, Ctrl+V to paste image"
        ></textarea>
      </div>
      {#if busy && !text.trim()}
        <button
          class="grid size-[3.25rem] place-items-center rounded-xl border border-[oklch(70%_0.18_25/0.45)] bg-[oklch(70%_0.18_25/0.12)] text-[var(--color-danger)] transition hover:bg-[oklch(70%_0.18_25/0.2)]"
          title="Interrupt"
          type="button"
          onclick={oninterrupt}
        >
          <Square size={18} />
        </button>
      {:else if busy}
        <button
          class="grid size-[3.25rem] place-items-center rounded-xl bg-gradient-to-br from-[oklch(64%_0.16_230)] to-[oklch(58%_0.18_260)] text-[var(--color-bg)] shadow-lg shadow-[oklch(64%_0.16_230/0.3)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
          disabled={!canSend}
          title={uploadingNow ? "Uploading…" : "Continue turn (Enter)"}
          type="submit"
        >
          <Send size={18} />
        </button>
      {:else}
        <button
          class="grid size-[3.25rem] place-items-center rounded-xl bg-gradient-to-br from-[oklch(72%_0.18_175)] to-[oklch(64%_0.16_320)] text-[var(--color-bg)] shadow-lg shadow-[oklch(72%_0.18_175/0.3)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
          disabled={!canSend}
          title={uploadingNow ? "Uploading…" : "Send (Enter)"}
          type="submit"
        >
          <Send size={18} />
        </button>
      {/if}
    </div>
  </div>
</form>
