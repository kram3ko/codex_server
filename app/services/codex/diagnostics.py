import time
from typing import Any

from app.services.codex.events import ChatEvent
from app.services.codex.transport import AppServerClient, Notification


type _ActiveItem = tuple[str | None, str | None, float | None]


class TurnDiagnostics:
    """Small in-memory state for explaining idle timeouts after the fact."""

    def __init__(self, *, thread_id: str | None, turn_id: str) -> None:
        now = time.monotonic()
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.started_at = now
        self.last_raw_at: float | None = None
        self.last_chat_event_at: float | None = None
        self.raw_count = 0
        self.chat_event_count = 0
        self.completed_items = 0
        self.stale_raw_count = 0
        self.noise_raw_count = 0
        self.last_raw_method = "none"
        self.last_raw_turn_id: str | None = None
        self.last_stale_method = "none"
        self.last_stale_turn_id: str | None = None
        self.last_stale_at: float | None = None
        self.last_stale_item_type: str | None = None
        self.last_stale_tool: str | None = None
        self.last_noise_method = "none"
        self.last_noise_at: float | None = None
        self.last_item_type: str | None = None
        self.last_tool: str | None = None
        self.last_chat_event_type = "none"
        self.turn_completed_seen = False
        self._active_items: dict[str, _ActiveItem] = {}

    def absorb_raw(self, note: Notification) -> None:
        now = time.monotonic()
        self.raw_count += 1
        self.last_raw_at = now
        self.last_raw_method = note.method
        self.last_raw_turn_id = note.turn_id
        if note.method == "turn/completed":
            self.turn_completed_seen = True

        item = note.params.get("item")
        if not isinstance(item, dict):
            return
        item_type = item.get("type")
        tool = item_label(item)
        self.last_item_type = item_type if isinstance(item_type, str) else None
        self.last_tool = tool
        if note.method == "item/started":
            self._active_items[item_key(item)] = (self.last_item_type, tool, now)
        elif note.method == "item/completed":
            self.completed_items += 1
            self._active_items.pop(item_key(item), None)

    def absorb_stale_raw(self, note: Notification) -> None:
        now = time.monotonic()
        self.stale_raw_count += 1
        self.last_stale_at = now
        self.last_stale_method = note.method
        self.last_stale_turn_id = note.turn_id
        item = note.params.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            self.last_stale_item_type = item_type if isinstance(item_type, str) else None
            self.last_stale_tool = item_label(item)

    def absorb_noise_raw(self, note: Notification) -> None:
        self.noise_raw_count += 1
        self.last_noise_at = time.monotonic()
        self.last_noise_method = note.method

    def absorb_chat_event(self, event: ChatEvent) -> None:
        self.chat_event_count += 1
        self.last_chat_event_at = time.monotonic()
        self.last_chat_event_type = type(event).__name__

    def snapshot(self, transport: AppServerClient) -> dict[str, Any]:
        now = time.monotonic()
        data: dict[str, Any] = {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "turn_age_s": round(now - self.started_at, 3),
            "raw_count": self.raw_count,
            "chat_event_count": self.chat_event_count,
            "completed_items": self.completed_items,
            "stale_raw_count": self.stale_raw_count,
            "noise_raw_count": self.noise_raw_count,
            "last_raw_method": self.last_raw_method,
            "last_raw_turn_id": self.last_raw_turn_id,
            "last_raw_age_s": age(now, self.last_raw_at),
            "last_stale_method": self.last_stale_method,
            "last_stale_turn_id": self.last_stale_turn_id,
            "last_stale_age_s": age(now, self.last_stale_at),
            "last_stale_item_type": self.last_stale_item_type,
            "last_stale_tool": self.last_stale_tool,
            "last_noise_method": self.last_noise_method,
            "last_noise_age_s": age(now, self.last_noise_at),
            "last_item_type": self.last_item_type,
            "last_tool": self.last_tool,
            "last_chat_event_type": self.last_chat_event_type,
            "last_chat_event_age_s": age(now, self.last_chat_event_at),
            "turn_completed_seen": self.turn_completed_seen,
        }
        active_item_type, active_tool, active_started_at = self.selected_active_item()
        data.update(
            {
                "active_items": len(self._active_items),
                "active_item_type": active_item_type,
                "active_tool": active_tool,
                "active_item_age_s": age(now, active_started_at),
            }
        )
        data.update(transport.diagnostic_snapshot())
        return data

    def selected_active_item(self) -> _ActiveItem:
        if not self._active_items:
            return (None, None, None)
        return max(self._active_items.values(), key=active_item_rank)

    def has_active_items(self) -> bool:
        return bool(self._active_items)


def age(now: float, at: float | None) -> float | None:
    return None if at is None else round(now - at, 3)


def item_key(item: dict[str, Any]) -> str:
    for key in ("id", "callId", "toolCallId"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return f"{item.get('type')}:{item_label(item)}"


def active_item_rank(item: _ActiveItem) -> tuple[int, float]:
    item_type, tool, started_at = item
    return (active_item_priority(item_type, tool), -(started_at or 0.0))


def active_item_priority(item_type: str | None, tool: str | None) -> int:
    if item_type in {
        "commandExecution",
        "mcpToolCall",
        "dynamicToolCall",
        "webSearch",
        "imageGeneration",
        "fileChange",
    }:
        return 30
    if tool not in {None, "reasoning", "agentMessage", "userMessage"}:
        return 20
    if item_type == "agentMessage":
        return 10
    if item_type == "reasoning":
        return 5
    return 0


def item_label(item: dict[str, Any]) -> str | None:
    for key in ("toolName", "tool", "name"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    item_type = item.get("type")
    if isinstance(item_type, str):
        match item_type:
            case "commandExecution":
                return "shell"
            case "fileChange":
                return "file_change"
            case "webSearch":
                return "web_search"
            case "imageGeneration":
                return "image_generation"
            case "imageView":
                return "image_view"
            case _:
                return item_type
    return None


__all__ = ["TurnDiagnostics", "active_item_rank", "age", "item_key", "item_label"]
