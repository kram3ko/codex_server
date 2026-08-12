from typing import Any

import structlog

from app.services.codex.shared import Method, ThreadChangeCallback, is_thread_not_found
from app.services.codex.transport import AppServerClient, AppServerError

log = structlog.get_logger(__name__)


class CodexThreadSession:
    def __init__(
        self,
        *,
        transport: AppServerClient,
        cwd: str,
        approval_policy: str,
        sandbox: str,
        initial_thread_id: str | None,
        on_thread_change: ThreadChangeCallback | None,
    ) -> None:
        self._transport = transport
        self._cwd = cwd
        self._approval_policy = approval_policy
        self._sandbox = sandbox
        self._thread_id = initial_thread_id
        self._thread_resumed_or_started = False
        self._on_thread_change = on_thread_change

    @property
    def current_thread_id(self) -> str | None:
        return self._thread_id

    async def ensure_thread(self) -> str:
        if self._thread_id is not None and self._thread_resumed_or_started:
            return self._thread_id

        if self._thread_id is not None:
            stale = self._thread_id
            if await self._try_resume(stale):
                self._thread_resumed_or_started = True
                return stale
            log.info("codex_thread_resume_failed_opening_new", stale_thread_id=stale)
            await self.reset_thread()

        return await self._open_new_thread()

    async def reset_thread(self) -> None:
        self._thread_id = None
        self._thread_resumed_or_started = False
        await self._emit_thread_change(None)

    async def read_thread(
        self,
        thread_id: str | None = None,
        *,
        include_turns: bool = True,
    ) -> dict[str, Any] | None:
        target = thread_id or self._thread_id
        if not target:
            return None
        try:
            result = await self._transport.request(
                Method.THREAD_READ,
                {"threadId": target, "includeTurns": include_turns},
            )
        except AppServerError as exc:
            if exc.code == -32601:
                log.info("codex_thread_read_unsupported", thread_id=target)
                return None
            log.warning(
                "codex_thread_read_failed",
                thread_id=target,
                code=exc.code,
                msg=str(exc),
            )
            return None
        if not isinstance(result, dict):
            return None
        return result

    async def inject_history(self, items: list[dict[str, Any]]) -> None:
        thread_id = self._thread_id
        if not thread_id or not items:
            return
        try:
            await self._transport.request(
                Method.THREAD_INJECT_ITEMS,
                {"threadId": thread_id, "items": items},
            )
        except AppServerError as exc:
            log.warning("codex_inject_history_failed", code=exc.code, msg=str(exc))
            return
        log.info("codex_history_injected", thread_id=thread_id, items=len(items))

    async def _try_resume(self, thread_id: str) -> bool:
        try:
            await self._transport.request(Method.THREAD_RESUME, {"threadId": thread_id})
        except AppServerError as exc:
            if is_thread_not_found(exc) or exc.code == -32601:
                return False
            raise
        log.info("codex_thread_resumed", thread_id=thread_id)
        return True

    async def _open_new_thread(self) -> str:
        result = await self._transport.request(
            Method.THREAD_START,
            {
                "cwd": self._cwd,
                "approvalPolicy": self._approval_policy,
                "sandbox": self._sandbox,
            },
        )
        thread_id: str = result["thread"]["id"]
        self._thread_id = thread_id
        self._thread_resumed_or_started = True
        log.info("codex_thread_opened", thread_id=thread_id)
        await self._emit_thread_change(thread_id)
        return thread_id

    async def _emit_thread_change(self, new_thread_id: str | None) -> None:
        if self._on_thread_change is None:
            return
        try:
            await self._on_thread_change(new_thread_id)
        except Exception as exc:
            log.warning(
                "codex_on_thread_change_failed",
                new=new_thread_id,
                error=str(exc),
            )


__all__ = ["CodexThreadSession"]
