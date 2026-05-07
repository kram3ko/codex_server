"""CodexClient tool guard for non-admin users.

Перевірка `_is_event_forbidden` — pure function, без WS/transport. Guard
працює і для `ToolCallEvent` (preemptive interrupt) і для `ToolResultEvent`
(приховуємо output навіть якщо subprocess встиг закінчити за race-window).
"""

from app.services.codex.client import _GUEST_FORBIDDEN_TOOLS, CodexClient
from app.services.codex.events import (
    DoneEvent,
    ErrorEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def _make_client(*, is_admin: bool) -> CodexClient:
    return CodexClient(
        url="ws://nope",
        cwd="/nope",
        approval_policy="never",
        sandbox="danger-full-access",
        is_admin=is_admin,
    )


def test_admin_passes_shell_call() -> None:
    client = _make_client(is_admin=True)
    assert client._is_event_forbidden(ToolCallEvent(name="shell", args={})) is False


def test_admin_passes_shell_result() -> None:
    client = _make_client(is_admin=True)
    assert client._is_event_forbidden(ToolResultEvent(name="shell", text="ok")) is False


def test_guest_blocked_from_shell_call() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(ToolCallEvent(name="shell", args={})) is True


def test_guest_blocked_from_shell_result() -> None:
    """Race window: subprocess міг закінчити до interrupt'а — приховуємо output."""
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(
        ToolResultEvent(name="shell", text="OPENAI_API_KEY=sk-..."),
    ) is True


def test_guest_blocked_from_file_change_call() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(ToolCallEvent(name="file_change", args={})) is True


def test_guest_blocked_from_file_change_result() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(ToolResultEvent(name="file_change", text="patched")) is True


def test_guest_allowed_image_generation() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(
        ToolCallEvent(name="image_generation", args={}),
    ) is False
    assert client._is_event_forbidden(
        ToolResultEvent(name="image_generation", text="kitten"),
    ) is False


def test_guest_allowed_web_search() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(ToolCallEvent(name="web_search", args={})) is False


def test_guest_allowed_mcp() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(ToolCallEvent(name="mcp", args={})) is False


def test_guest_token_event_not_blocked() -> None:
    """TokenEvent — звичайний потік agentMessage, його блокувати не треба."""
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(TokenEvent(delta="привіт")) is False


def test_guest_done_event_not_blocked() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(DoneEvent(final_text="готово")) is False


def test_guest_error_event_not_blocked() -> None:
    client = _make_client(is_admin=False)
    assert client._is_event_forbidden(ErrorEvent(code="x", detail="y")) is False


def test_forbidden_set_is_exact() -> None:
    """Якщо заборонене розширюємо — explicitly update цей тест, щоб не proшло
    silent (CI знайде розбіжність)."""
    assert frozenset({"shell", "file_change"}) == _GUEST_FORBIDDEN_TOOLS
