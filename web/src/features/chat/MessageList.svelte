<script lang="ts">
  import { tick } from "svelte";

  import type { Attachment as ChatAttachment } from "../../gen/codex/v1/chat_pb";
  import type { Message as ChatMessage } from "../../gen/codex/v1/message_pb";
  import Attachment from "./Attachment.svelte";
  import CompletedTools from "./CompletedTools.svelte";
  import Message from "./Message.svelte";
  import ToolCall, { type ToolEvent } from "./ToolCall.svelte";

  let {
    messages,
    streamingClientId,
    tools,
    attachments,
    draftStartedAt
  }: {
    messages: ChatMessage[];
    streamingClientId: string | null;
    tools: ToolEvent[];
    attachments: ChatAttachment[];
    draftStartedAt?: number;
  } = $props();

  function clientIdOf(message: ChatMessage): string | undefined {
    const meta = message.meta as Record<string, unknown> | undefined;
    const cid = meta?.client_id;
    return typeof cid === "string" ? cid : undefined;
  }

  function messageKey(message: ChatMessage): string {
    return clientIdOf(message) ?? message.id.toString();
  }

  let container = $state<HTMLDivElement | null>(null);
  let stickToBottom = $state(true);

  const runningTools = $derived(tools.filter((t) => t.status === "running"));
  const completedTools = $derived(tools.filter((t) => t.status !== "running"));
  const currentToolName = $derived(runningTools[0]?.name);



  // Напрямок скролу — найнадійніший signal: user-up → unstick; back-to-bottom
  // → re-stick. Programmatic `scrollTop = scrollHeight` завжди йде ВНИЗ, тож
  // воно нічого не ламає.
  let lastScrollTop = 0;
  let lastMessagesLen = 0;

  function onscroll() {
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    if (scrollTop < lastScrollTop) {
      stickToBottom = false;
    } else if (scrollHeight - scrollTop - clientHeight < 50) {
      stickToBottom = true;
    }
    lastScrollTop = scrollTop;
  }

  // Нове повідомлення в історії (юзер натиснув Send або turn finalize'нувся) —
  // форс-stickToBottom, навіть якщо юзер до того скролив угору.
  $effect(() => {
    if (messages.length > lastMessagesLen) {
      stickToBottom = true;
    }
    lastMessagesLen = messages.length;
  });

  // Snap to bottom on any list/tool/attachment change while sticking.
  $effect(() => {
    void messages;
    void tools;
    void attachments;
    if (!stickToBottom) return;
    tick().then(() => {
      if (container) container.scrollTop = container.scrollHeight;
    });
  });
</script>

<div
  bind:this={container}
  {onscroll}
  class="min-h-0 flex-1 overflow-y-auto scroll-smooth px-5 py-4"
>
  <div class="mx-auto flex max-w-5xl flex-col gap-4">
    {#each messages as message (messageKey(message))}
      {@const streaming = clientIdOf(message) === streamingClientId}
      <Message
        {message}
        {streaming}
        startedAt={streaming ? draftStartedAt : undefined}
        currentToolName={streaming ? currentToolName : undefined}
      />
      {#if streaming && tools.length}
        <div class="ml-11 max-w-[760px] space-y-2">
          {#each runningTools as tool (tool.id)}
            <ToolCall event={tool} />
          {/each}
          <CompletedTools tools={completedTools} />
        </div>
      {/if}
      {#if streaming && attachments.length}
        <div class="ml-11 grid max-w-[760px] grid-cols-1 gap-2 md:grid-cols-2">
          {#each attachments as attachment (`${attachment.kind}:${attachment.source}`)}
            <Attachment {attachment} />
          {/each}
        </div>
      {/if}
    {/each}
  </div>
</div>
