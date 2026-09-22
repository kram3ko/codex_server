"""CodexPrefsService — validation against the model catalog + turn-option resolution."""

import pytest

from app.services.codex.sidecar import SidecarName
from app.services.codex_prefs.catalog import CodexModelCatalog
from app.services.codex_prefs.schemas import (
    CodexModel,
    CodexModelIn,
    CodexPreferences,
    ReasoningEffortOption,
)
from app.services.codex_prefs.service import (
    CodexPrefsService,
    UnknownModelError,
    UnsupportedReasoningEffortError,
)

_ASTRA = CodexModel(
    id="gpt-6-astra",
    display_name="GPT-6 Astra",
    description="",
    is_default=True,
    default_reasoning_effort="medium",
    supported_reasoning_efforts=(
        ReasoningEffortOption(value="low", description=""),
        ReasoningEffortOption(value="medium", description=""),
        ReasoningEffortOption(value="high", description=""),
    ),
)


class _StaticCatalog(CodexModelCatalog):
    async def list(self, sidecar: SidecarName) -> list[CodexModel]:
        return [_ASTRA]


def _service() -> CodexPrefsService:
    return CodexPrefsService(_StaticCatalog())


async def test_validate_accepts_default_prefs() -> None:
    await _service()._validate(SidecarName.ADMIN, CodexPreferences())


async def test_validate_checks_effort_against_default_model() -> None:
    with pytest.raises(UnsupportedReasoningEffortError):
        await _service()._validate(SidecarName.ADMIN, CodexPreferences(reasoning_effort="ultra"))
    await _service()._validate(SidecarName.ADMIN, CodexPreferences(reasoning_effort="low"))


async def test_resolve_drops_fallback_unsupported_by_model(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fallback_reasoning_effort", lambda: "ultra")

    async def _prefs(session: object, user_id: int) -> CodexPreferences:
        return CodexPreferences()

    monkeypatch.setattr(svc, "get", _prefs)
    options = await svc.resolve_turn_options(None, 1, SidecarName.ADMIN)
    assert options.reasoning_effort is None


async def test_resolve_keeps_supported_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service()
    monkeypatch.setattr(svc, "fallback_reasoning_effort", lambda: "high")

    async def _prefs(session: object, user_id: int) -> CodexPreferences:
        return CodexPreferences()

    monkeypatch.setattr(svc, "get", _prefs)
    options = await svc.resolve_turn_options(None, 1, SidecarName.ADMIN)
    assert options.reasoning_effort == "high"


async def test_validate_rejects_unknown_model() -> None:
    with pytest.raises(UnknownModelError):
        await _service()._validate(SidecarName.ADMIN, CodexPreferences(model="nope"))


async def test_validate_rejects_unsupported_effort() -> None:
    prefs = CodexPreferences(model="gpt-6-astra", reasoning_effort="xhigh")
    with pytest.raises(UnsupportedReasoningEffortError):
        await _service()._validate(SidecarName.ADMIN, prefs)


async def test_validate_accepts_supported_effort() -> None:
    prefs = CodexPreferences(model="gpt-6-astra", reasoning_effort="high")
    await _service()._validate(SidecarName.ADMIN, prefs)


def test_catalog_parse_drops_hidden_models() -> None:
    raw = [
        {"id": "a", "displayName": "A", "hidden": False},
        {"id": "b", "displayName": "B", "hidden": True},
    ]
    assert [m.id for m in CodexModelCatalog._parse(raw)] == ["a"]


def test_model_in_maps_reasoning_efforts() -> None:
    parsed = CodexModelIn.model_validate(
        {
            "id": "x",
            "displayName": "X",
            "supportedReasoningEfforts": [{"reasoningEffort": "low", "description": "fast"}],
        }
    ).to_domain()
    assert parsed.supported_reasoning_efforts[0] == ReasoningEffortOption(
        value="low", description="fast"
    )
