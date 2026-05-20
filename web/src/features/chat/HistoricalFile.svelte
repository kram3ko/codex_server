<script lang="ts">
  import { FileText, Download } from "lucide-svelte";

  import { uploadsClient } from "../../shared/lib/clients";

  let { uploadId }: { uploadId: number } = $props();

  let url = $state("");
  let filename = $state("");
  let size = $state(0);
  let failed = $state(false);

  $effect(() => {
    failed = false;
    url = "";
    filename = "";
    size = 0;
    uploadsClient
      .getPresigned({ target: { case: "uploadId", value: BigInt(uploadId) } })
      .then((response) => {
        url = response.url;
        filename = response.upload?.filename ?? "file";
        size = Number(response.upload?.size ?? 0);
      })
      .catch(() => {
        failed = true;
      });
  });

  function fmtSize(bytes: number): string {
    if (bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`;
  }
</script>

{#if failed}
  <div class="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text-muted)]">
    <FileText size={15} />
    File unavailable
  </div>
{:else if url}
  <a
    href={url}
    target="_blank"
    rel="noopener noreferrer"
    class="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm transition hover:bg-[var(--color-surface-hover)]"
  >
    <FileText size={16} class="shrink-0 text-[var(--color-text-muted)]" />
    <span class="flex-1 truncate" title={filename}>{filename}</span>
    {#if size > 0}
      <span class="shrink-0 text-xs text-[var(--color-text-muted)]">{fmtSize(size)}</span>
    {/if}
    <Download size={14} class="shrink-0 text-[var(--color-text-muted)]" />
  </a>
{:else}
  <div class="flex h-10 items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-[var(--color-text-muted)]">
    <FileText size={15} class="animate-pulse" />
  </div>
{/if}
