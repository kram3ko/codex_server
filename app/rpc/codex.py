"""CodexService — model catalog + per-user preferences."""

from typing import override

import websockets
from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import codex_pb2
from app.grpc_generated.codex.v1.codex_connect import CodexService as CodexProtocol
from app.models import UserRole
from app.rpc._auth import require_user
from app.services.codex.sidecar import SidecarName
from app.services.codex.transport import AppServerError
from app.services.codex_prefs.default import codex_prefs_service
from app.services.codex_prefs.schemas import CodexModel, CodexPreferences
from app.services.codex_prefs.service import (
    UnknownModelError,
    UnsupportedReasoningEffortError,
)

_SIDECAR_ERRORS = (AppServerError, websockets.WebSocketException, OSError, TimeoutError)


def _sidecar_for(role: UserRole) -> SidecarName:
    return SidecarName.ADMIN if role == UserRole.ADMIN else SidecarName.GUEST


def _model_to_pb(m: CodexModel) -> codex_pb2.CodexModel:
    return codex_pb2.CodexModel(
        id=m.id,
        display_name=m.display_name,
        description=m.description,
        is_default=m.is_default,
        default_reasoning_effort=m.default_reasoning_effort,
        supported_reasoning_efforts=[
            codex_pb2.ReasoningEffortOption(value=o.value, description=o.description)
            for o in m.supported_reasoning_efforts
        ],
    )


def _prefs_to_pb(p: CodexPreferences) -> codex_pb2.CodexPreferences:
    msg = codex_pb2.CodexPreferences()
    if p.model is not None:
        msg.model = p.model
    if p.reasoning_effort is not None:
        msg.reasoning_effort = p.reasoning_effort
    return msg


class CodexRPC(CodexProtocol):
    @override
    async def list_models(
        self,
        request: codex_pb2.ListModelsRequest,
        ctx: RequestContext,
    ) -> codex_pb2.ListModelsResponse:
        del request
        user = await require_user(ctx)
        try:
            models = await codex_prefs_service.list_models(_sidecar_for(user.role))
        except _SIDECAR_ERRORS as exc:
            raise ConnectError(Code.UNAVAILABLE, f"sidecar unavailable: {exc}") from exc
        return codex_pb2.ListModelsResponse(
            models=[_model_to_pb(m) for m in models],
            fallback_reasoning_effort=codex_prefs_service.fallback_reasoning_effort() or "",
        )

    @override
    async def get_preferences(
        self,
        request: codex_pb2.GetPreferencesRequest,
        ctx: RequestContext,
    ) -> codex_pb2.CodexPreferences:
        del request
        user = await require_user(ctx)
        async with SessionLocal() as db:
            prefs = await codex_prefs_service.get(db, user.id)
        return _prefs_to_pb(prefs)

    @override
    async def update_preferences(
        self,
        request: codex_pb2.UpdatePreferencesRequest,
        ctx: RequestContext,
    ) -> codex_pb2.CodexPreferences:
        user = await require_user(ctx)
        prefs = CodexPreferences(
            model=request.model.strip() or None if request.HasField("model") else None,
            reasoning_effort=(
                request.reasoning_effort.strip() or None
                if request.HasField("reasoning_effort")
                else None
            ),
        )
        try:
            async with SessionLocal() as db:
                saved = await codex_prefs_service.update(
                    db, user.id, _sidecar_for(user.role), prefs
                )
                await db.commit()
        except (UnknownModelError, UnsupportedReasoningEffortError) as exc:
            raise ConnectError(Code.INVALID_ARGUMENT, str(exc)) from exc
        except _SIDECAR_ERRORS as exc:
            raise ConnectError(Code.UNAVAILABLE, f"sidecar unavailable: {exc}") from exc
        return _prefs_to_pb(saved)
