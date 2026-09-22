"""Per-user Codex model / reasoning-effort preference. Tx boundary at the caller."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CodexPreference
from app.services.codex.sidecar import SidecarName
from app.services.codex_prefs.catalog import CodexModelCatalog
from app.services.codex_prefs.schemas import CodexModel, CodexPreferences, TurnOptions


class UnknownModelError(ValueError):
    def __init__(self, model_id: str) -> None:
        super().__init__(f"unknown model: {model_id}")
        self.model_id = model_id


class UnsupportedReasoningEffortError(ValueError):
    def __init__(self, model_id: str, effort: str) -> None:
        super().__init__(f"model {model_id} does not support reasoning effort {effort}")
        self.model_id = model_id
        self.effort = effort


class CodexPrefsService:
    def __init__(self, catalog: CodexModelCatalog) -> None:
        self._catalog = catalog

    async def list_models(self, sidecar: SidecarName) -> list[CodexModel]:
        return await self._catalog.list(sidecar)

    def fallback_reasoning_effort(self) -> str | None:
        return settings.CODEX_REASONING_EFFORT or None

    async def get(self, session: AsyncSession, user_id: int) -> CodexPreferences:
        row = await self._row(session, user_id)
        if row is None:
            return CodexPreferences()
        return CodexPreferences(model=row.model, reasoning_effort=row.reasoning_effort)

    async def update(
        self,
        session: AsyncSession,
        user_id: int,
        sidecar: SidecarName,
        prefs: CodexPreferences,
    ) -> CodexPreferences:
        await self._validate(sidecar, prefs)
        stmt = (
            insert(CodexPreference)
            .values(user_id=user_id, model=prefs.model, reasoning_effort=prefs.reasoning_effort)
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={"model": prefs.model, "reasoning_effort": prefs.reasoning_effort},
            )
        )
        await session.execute(stmt)
        return prefs

    async def resolve_turn_options(
        self, session: AsyncSession, user_id: int, sidecar: SidecarName
    ) -> TurnOptions:
        prefs = await self.get(session, user_id)
        if prefs.reasoning_effort is not None:
            return TurnOptions(model=prefs.model, reasoning_effort=prefs.reasoning_effort)
        model = await self._effective_model(sidecar, prefs.model)
        fallback = self.fallback_reasoning_effort()
        if fallback is None or (model is not None and not _supports(model, fallback)):
            return TurnOptions(model=prefs.model)
        return TurnOptions(model=prefs.model, reasoning_effort=fallback)

    async def _validate(self, sidecar: SidecarName, prefs: CodexPreferences) -> None:
        model = await self._effective_model(sidecar, prefs.model)
        if prefs.model is not None and model is None:
            raise UnknownModelError(prefs.model)
        if prefs.reasoning_effort is None or model is None:
            return
        if not _supports(model, prefs.reasoning_effort):
            raise UnsupportedReasoningEffortError(model.id, prefs.reasoning_effort)

    async def _effective_model(
        self, sidecar: SidecarName, model_id: str | None
    ) -> CodexModel | None:
        if model_id is not None:
            return await self._catalog.find(sidecar, model_id)
        models = await self._catalog.list(sidecar)
        return next((m for m in models if m.is_default), models[0] if models else None)

    @staticmethod
    async def _row(session: AsyncSession, user_id: int) -> CodexPreference | None:
        return (
            await session.execute(select(CodexPreference).where(CodexPreference.user_id == user_id))
        ).scalar_one_or_none()


def _supports(model: CodexModel, effort: str) -> bool:
    supported = {o.value for o in model.supported_reasoning_efforts}
    return not supported or effort in supported
