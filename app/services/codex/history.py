"""ORM Message → Codex CLI thread/inject_items payload (Responses API items)."""

from typing import Any

from app.models import Message, MessageRole

# Roles we replay back into Codex; TOOL/SYSTEM stay model-internal.
_REPLAYABLE = {MessageRole.USER, MessageRole.ASSISTANT}


def messages_to_history_items(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role not in _REPLAYABLE:
            continue
        text = (msg.text or "").strip()
        if not text:
            continue
        if msg.role is MessageRole.USER:
            items.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            })
        else:
            items.append({
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            })
    return items
