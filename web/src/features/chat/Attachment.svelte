<script lang="ts">
  import { FileDown } from "lucide-svelte";

  import type { Attachment as ChatAttachment } from "../../gen/codex/v1/chat_pb";

  // Streamed ToolResult attachments — server rewrites image_generation
  // file paths to `/generated/<hash>.png` (FastAPI StaticFiles); others
  // arrive as http(s) URLs. Both render directly without presigned hop.
  let { attachment }: { attachment: ChatAttachment } = $props();
  const url = $derived(attachment.source);
  const failed = $derived(!/^(https?:\/\/|\/)/.test(attachment.source));
</script>

{#if failed}
  <div class="rounded-md border border-[#e7c9c1] bg-[#fff5f2] p-3 text-sm text-[#a33a2b]">
    Attachment unavailable
  </div>
{:else if attachment.kind === "image"}
  <figure class="overflow-hidden rounded-md border border-[#d9d3c8] bg-white">
    <img class="max-h-96 w-full object-contain" src={url} alt={attachment.caption || "attachment"} />
    {#if attachment.caption}
      <figcaption class="border-t border-[#d9d3c8] px-3 py-2 text-xs text-[#60706a]">{attachment.caption}</figcaption>
    {/if}
  </figure>
{:else if attachment.kind === "audio"}
  <audio class="w-full" controls src={url}></audio>
{:else}
  <a class="inline-flex items-center gap-2 rounded-md border border-[#d9d3c8] bg-white px-3 py-2 text-sm hover:bg-[#f1eee8]" href={url} download>
    <FileDown size={15} />
    {attachment.caption || "Download"}
  </a>
{/if}
