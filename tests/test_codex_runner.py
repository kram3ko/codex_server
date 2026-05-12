"""`open_codex_turn` quarantine + thread_id передача у CodexClient.

Перевіряємо що stored thread_id з chats.codex_thread_id передається у клієнт
як initial_thread_id, але **тільки якщо** Redis не помітив його як broken.
"""

import pytest

from app.services.codex import runner


class _FakeChat:
    def __init__(self, codex_thread_id: str | None) -> None:
        self.codex_thread_id = codex_thread_id
        self.id = 1


class _RecordingCache:
    def __init__(self, quarantined: set[str]) -> None:
        self._quarantined = quarantined

    async def get(self, key: str) -> bytes | None:
        for thread_id in self._quarantined:
            if key.endswith(thread_id):
                return b"broken"
        return None


class _RecordingClient:
    last_kwargs: dict | None = None

    def __init__(self, **kwargs) -> None:
        type(self).last_kwargs = kwargs

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def ensure_thread(self) -> str: ...

    @property
    def current_thread_id(self) -> str | None:
        return None


@pytest.fixture
def patch_runner(monkeypatch: pytest.MonkeyPatch):
    _RecordingClient.last_kwargs = None

    async def _fake_load(chat_id: int) -> str | None:
        return _FakeChat("stored-tid").codex_thread_id

    monkeypatch.setattr(runner, "_load_stored_thread_id", _fake_load)
    monkeypatch.setattr(runner, "CodexClient", _RecordingClient)
    monkeypatch.setattr(runner.settings, "CODEX_THREAD_REUSE_ENABLED", True)


@pytest.mark.asyncio
async def test_open_codex_turn_passes_stored_thread_id(
    monkeypatch: pytest.MonkeyPatch, patch_runner: None
) -> None:
    monkeypatch.setattr(runner, "cache", _RecordingCache(quarantined=set()))

    async with runner.open_codex_turn(1, is_admin=True, seed_history=False):
        pass

    assert _RecordingClient.last_kwargs is not None
    assert _RecordingClient.last_kwargs["initial_thread_id"] == "stored-tid"


@pytest.mark.asyncio
async def test_open_codex_turn_skips_quarantined_thread(
    monkeypatch: pytest.MonkeyPatch, patch_runner: None
) -> None:
    monkeypatch.setattr(runner, "cache", _RecordingCache(quarantined={"stored-tid"}))

    async with runner.open_codex_turn(1, is_admin=True, seed_history=False):
        pass

    assert _RecordingClient.last_kwargs is not None
    assert _RecordingClient.last_kwargs["initial_thread_id"] is None


@pytest.mark.asyncio
async def test_open_codex_turn_skips_thread_when_reuse_disabled(
    monkeypatch: pytest.MonkeyPatch, patch_runner: None
) -> None:
    monkeypatch.setattr(runner.settings, "CODEX_THREAD_REUSE_ENABLED", False)
    monkeypatch.setattr(runner, "cache", _RecordingCache(quarantined=set()))

    async with runner.open_codex_turn(1, is_admin=True, seed_history=False):
        pass

    assert _RecordingClient.last_kwargs is not None
    assert _RecordingClient.last_kwargs["initial_thread_id"] is None
