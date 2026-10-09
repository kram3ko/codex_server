"""CodexTurnSession — turn/start params, paginated turn probe, model catalog."""

from collections.abc import Callable
from typing import Any

from app.services.codex.shared import Method
from app.services.codex.thread import CodexThreadSession
from app.services.codex.transport import AppServerClient
from app.services.codex.turn import CodexTurnSession


class _ScriptedTransport(AppServerClient):
    def __init__(self, script: Callable[[str, dict[str, Any]], Any]) -> None:
        super().__init__(url="ws://test")
        self._script = script
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, params or {}))
        return self._script(method, params or {})


def _session(transport: _ScriptedTransport, **kwargs: Any) -> CodexTurnSession:
    threads = CodexThreadSession(
        transport=transport,
        cwd="/tmp",
        approval_policy="never",
        sandbox="danger-full-access",
        initial_thread_id="t1",
        on_thread_change=None,
    )
    return CodexTurnSession(transport=transport, threads=threads, **kwargs)


def test_turn_params_include_model_and_effort() -> None:
    transport = _ScriptedTransport(lambda _m, _p: {})
    session = _session(transport, model="gpt-6-astra", reasoning_effort="high")
    params = session._build_turn_params("t1", [{"type": "text", "text": "hi"}])
    assert params["model"] == "gpt-6-astra"
    assert params["effort"] == "high"


def test_turn_params_omit_unset_overrides() -> None:
    transport = _ScriptedTransport(lambda _m, _p: {})
    session = _session(transport)
    params = session._build_turn_params("t1", [{"type": "text", "text": "hi"}])
    assert "model" not in params
    assert "effort" not in params


async def test_probe_pages_through_turns_list() -> None:
    pages = {
        None: {"data": [{"id": "new", "status": "inProgress"}], "nextCursor": "c1"},
        "c1": {"data": [{"id": "old", "status": "completed"}], "nextCursor": None},
    }

    def script(method: str, params: dict[str, Any]) -> Any:
        assert method == Method.THREAD_TURNS_LIST
        assert params["itemsView"] == "notLoaded"
        return pages[params.get("cursor")]

    transport = _ScriptedTransport(script)
    session = _session(transport)
    assert await session.probe_turn_status("t1", "old") == "completed"
    assert len(transport.calls) == 2


async def test_probe_returns_none_when_turn_missing() -> None:
    transport = _ScriptedTransport(lambda _m, _p: {"data": [], "nextCursor": None})
    session = _session(transport)
    assert await session.probe_turn_status("t1", "ghost") is None


async def test_list_models_follows_cursor() -> None:
    pages = {
        None: {"data": [{"id": "a"}], "nextCursor": "n"},
        "n": {"data": [{"id": "b"}], "nextCursor": None},
    }
    transport = _ScriptedTransport(lambda _m, p: pages[p.get("cursor")])
    session = _session(transport)
    assert [m["id"] for m in await session.list_models()] == ["a", "b"]


def _thread_session(transport: _ScriptedTransport, **kwargs: Any) -> CodexThreadSession:
    return CodexThreadSession(
        transport=transport,
        cwd="/tmp",
        approval_policy="never",
        sandbox="danger-full-access",
        on_thread_change=None,
        **kwargs,
    )


async def test_thread_start_and_resume_carry_thread_config() -> None:
    config = {"mcp_servers.codex_app": {"http_headers": {"X-Codex-Authz": "tok"}}}
    transport = _ScriptedTransport(lambda _m, _p: {"thread": {"id": "t-new"}})

    await _thread_session(transport, initial_thread_id=None, thread_config=config).ensure_thread()
    await _thread_session(transport, initial_thread_id="t1", thread_config=config).ensure_thread()

    methods = [m for m, _ in transport.calls]
    assert methods == [Method.THREAD_START, Method.THREAD_RESUME]
    assert all(p["config"] == config for _, p in transport.calls)


async def test_thread_params_omit_config_when_unset() -> None:
    transport = _ScriptedTransport(lambda _m, _p: {"thread": {"id": "t-new"}})
    await _thread_session(transport, initial_thread_id=None).ensure_thread()
    assert "config" not in transport.calls[0][1]
