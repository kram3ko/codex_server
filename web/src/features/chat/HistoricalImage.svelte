<script lang="ts">
  import { Image as ImageIcon } from "lucide-svelte";

  import { uploadsClient } from "../../shared/lib/clients";

  let { uploadId }: { uploadId: number } = $props();

  let url = $state("");
  let failed = $state(false);

  $effect(() => {
    failed = false;
    url = "";
    uploadsClient
      .getPresigned({ target: { case: "uploadId", value: BigInt(uploadId) } })
      .then((response) => {
        url = response.url;
      })
      .catch(() => {
        failed = true;
      });
  });
</script>

{#if failed}
  <div class="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text-muted)]">
    <ImageIcon size={15} />
    Image unavailable
  </div>
{:else if url}
  <figure class="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
    <img class="max-h-96 w-full object-contain" src={url} alt="Generated result" />
  </figure>
{:else}
  <div class="grid h-48 place-items-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)]">
    <ImageIcon size={20} class="animate-pulse" />
  </div>
{/if}
