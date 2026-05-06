"""Тонкий JSON-RPC 2.0 клієнт поверх `codex app-server` WebSocket.

Реалізує мінімум потрібний для одного chat-турну: initialize → thread/start →
turn/start → стрім notifications → close. Pending requests track'аться по
числовому id; notifications кладуться в `asyncio.Queue` для consumer'a.

Transport: WebSocket, по одному JSON-RPC повідомленню на frame. Час життя
з'єднання = одна WS-сесія користувача (або одна conversation).
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import websockets

logger = logging.getLogger(__name__)

_JSONRPC_VERSION = "2.0"
_DEFAULT_REQUEST_TIMEOUT = 60.0
_NOTIFICATION_QUEUE_MAX = 1000


class AppServerError(RuntimeError):
    """JSON-RPC error response від app-server."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.data = data


@dataclass(frozen=True, slots=True)
class Notification:
    """Server-initiated notification (no id, no response expected)."""

    method: str
    params: dict[str, Any]


class AppServerClient:
    """Володіє одним WS до Codex app-server + JSON-RPC loop'ом."""

    def __init__(
        self,
        url: str,
        auth_token: str | None = None,
        request_timeout: float = _DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._url = url
        self._auth_token = auth_token
        self._request_timeout = request_timeout
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        # None — close-sentinel; кладеться у `close()` щоб `notifications()`
        # консьюмер прокинувся без polling-таймауту.
        self._notifications: asyncio.Queue[Notification | None] = asyncio.Queue(
            maxsize=_NOTIFICATION_QUEUE_MAX,
        )
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reader_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        if self._ws is not None:
            raise RuntimeError("AppServerClient already connected")
        headers = (
            {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else None
        )
        logger.info("app_server_connecting url=%s authenticated=%s", self._url, bool(self._auth_token))
        self._ws = await websockets.connect(
            self._url,
            max_size=100 * 1024 * 1024,
            additional_headers=headers,
        )
        self._reader_task = asyncio.create_task(self._read_loop(), name="codex_app_server_reader")
        logger.info("app_server_connected")

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._ensure_open()
        req_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": _JSONRPC_VERSION,
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        await self._send(payload)
        try:
            return await asyncio.wait_for(future, timeout=self._request_timeout)
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._ensure_open()
        payload = {
            "jsonrpc": _JSONRPC_VERSION,
            "method": method,
            "params": params or {},
        }
        await self._send(payload)

    async def notifications(self) -> AsyncIterator[Notification]:
        while True:
            note = await self._notifications.get()
            if note is None:
                return
            yield note

    async def close(self) -> None:
        """Порядок: stop producer → close transport → wake consumer →
        fail pending. Інакше sentinel race з notifications."""
        if self._closed:
            return
        logger.info("app_server_closing pending=%d", len(self._pending))
        self._closed = True

        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()

        try:
            self._notifications.put_nowait(None)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._notifications.get_nowait()
            self._notifications.put_nowait(None)

        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("AppServerClient closed"))
        self._pending.clear()

    def _ensure_open(self) -> None:
        if self._closed or self._ws is None:
            raise RuntimeError("AppServerClient is not connected")

    async def _send(self, payload: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                self._dispatch(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed:
            logger.info("app_server_ws_closed")
        except Exception as exc:  # noqa: BLE001 — логнути будь-що і завершитись
            logger.error("app_server_reader_error error=%s", exc)
        finally:
            self._closed = True

    def _dispatch(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("app_server_bad_frame frame=%s", raw[:200])
            return

        if "id" in message and message["id"] is not None:
            self._resolve_response(message)
        elif "method" in message:
            note = Notification(
                method=message["method"],
                params=message.get("params") or {},
            )
            try:
                self._notifications.put_nowait(note)
            except asyncio.QueueFull:
                logger.warning(
                    "app_server_notification_dropped method=%s queue_size=%d",
                    note.method,
                    self._notifications.qsize(),
                )
        else:
            logger.warning("app_server_unknown_message keys=%s", list(message.keys()))

    def _resolve_response(self, message: dict[str, Any]) -> None:
        req_id = message["id"]
        future = self._pending.get(req_id)
        if future is None or future.done():
            return
        if "error" in message:
            err = message["error"]
            future.set_exception(
                AppServerError(
                    code=err.get("code", -1),
                    message=err.get("message", ""),
                    data=err.get("data"),
                ),
            )
        else:
            future.set_result(message.get("result"))
