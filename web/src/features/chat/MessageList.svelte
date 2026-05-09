<script lang="ts">
  import { tick } from "svelte";

  import type { Attachment as ChatAttachment } from "../../gen/codex/v1/chat_pb";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import Attachment from "./Attachment.svelte";
  import Message from "./Message.svelte";
  import ToolCall, { type ToolEvent } from "./ToolCall.svelte";

  let {
    messages,
    draft,
    tools,
    attachments,
    draftStartedAt
  }: {
    messages: ChatMessage[];
    draft: ChatMessage | null;
    tools: ToolEvent[];
    attachments: ChatAttachment[];
    draftStartedAt?: number;
  } = $props();

  let container = $state<HTMLDivElement | null>(null);
  let stickToBottom = $state(true);

  function onscroll() {
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    // <80px from bottom — вважаємо що юзер хоче бачити нові, autoscroll on
    stickToBottom = scrollHeight - scrollTop - clientHeight < 80;
  }

  // Snap to bottom on any list/draft/tool/attachment change while sticking.
  $effect(() => {
    void messages;
    void draft?.text;
    void tools;
    void attachments;
    if (!stickToBottom) return;
    tick().then(() => {
      if (container) container.scrollTop = container.scrollHeight;
    });
  });
</script>

<div bind:this={container} {onscroll} class="min-h-0 flex-1 overflow-y-auto scroll-smooth px-5 py-4">
  <div class="mx-auto flex max-w-5xl flex-col gap-4">
    {#each messages as message (message.id.toString())}
      <Message {message} />
    {/each}

    {#if tools.length}
      <div class="ml-11 max-w-[760px] space-y-2">
        {#each tools as tool (tool.id)}
          <ToolCall event={tool} />
        {/each}
      </div>
    {/if}

    {#if attachments.length}
      <div class="ml-11 grid max-w-[760px] grid-cols-1 gap-2 md:grid-cols-2">
        {#each attachments as attachment (`${attachment.kind}:${attachment.source}`)}
          <Attachment {attachment} />
        {/each}
      </div>
    {/if}

    {#if draft}
      <Message message={draft} streaming startedAt={draftStartedAt} />
    {/if}
  </div>
</div>
