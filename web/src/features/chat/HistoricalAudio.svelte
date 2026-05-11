<script lang="ts">
  import { Volume2 } from "lucide-svelte";

  import { uploadsClient } from "../../shared/lib/clients";

  let { uploadId, autoplay = false }: { uploadId: number; autoplay?: boolean } = $props();

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
    <Volume2 size={15} />
    Audio unavailable
  </div>
{:else if url}
  <audio
    class="w-full"
    src={url}
    controls
    {autoplay}
    preload="metadata"
  ></audio>
{/if}
