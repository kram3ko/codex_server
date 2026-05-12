"""Тонкий JSON-RPC 2.0 клієнт поверх `codex app-server` WebSocket.

Реалізує мінімум для chat-турну: initialize → thread/start → turn/start →
notifications stream → close. Pending requests track'аться по id; notifications
доставляються `NotificationHandler`-callback'у (зазвичай — `TurnRouter`),
який маршрутизує їх по turn_id. Transport нічого не знає про турни.

Lifecycle: `connect()` ідемпотентний; `close()` final, reconnect після нього
заборонений. Якщо WS падає сам по собі — `is_connected=False`, пендінги
дофейлюються, `on_close` callback кличеться.
"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import orjson
import structlog
import websockets

log = structlog.get_logger(__name__)

_JSONRPC_VERSION = "2.0"
_DEFAULT_REQUEST_TIMEOUT = 60.0
# Sidecar може transient'но не resolv'итись (DNS прогрів) одразу після свого старту.
_CONNECT_RETRIES = 3
_CONNECT_BACKOFF_S = 1.5


class AppServerError(RuntimeError):
    """JSON-RPC error response від app-server."""

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.data = data


@dataclass(frozen=True, slots=True)
class Notification:
    """Server-initiated notification (no id, no response expected).

    `turn_id` витягається з `params.turnId` для турн-скоупних нот; для
    session-level (`initialized` тощо) лишається None — router їх дропає.
    """

    method: str
    params: dict[str, Any]
    turn_id: str | None


NotificationHandler = Callable[[Notification], None]
CloseHandler = Callable[[], None]


class AppServerClient:
    """Володіє одним WS до Codex app-server + JSON-RPC loop'ом.

    Не тримає буферу notifications — кожна доставляється у `notification_handler`
    синхронно з read-loop. Composition: `TurnRouter` підписується через
    `set_notification_handler` і фановтить ноти по turn_id.
    """

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
        self._ws: websockets.ClientConnection | None = None
        self._reader_task: asyncio.Task | None = None
        self._explicit_close = False
        self._connect_lock = asyncio.Lock()
        self._notification_handler: NotificationHandler | None = None
        self._close_handler: CloseHandler | None = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._explicit_close

    def set_notification_handler(self, handler: NotificationHandler) -> None:
        """Reg один handler. Викликається синхронно з read-loop на кожну ноту."""
        self._notification_handler = handler

    def set_close_handler(self, handler: CloseHandler) -> None:
        """Reg один handler. Викликається коли transport остаточно або тимчасово
        втратив зв'язок (WS закритий/reader умер). Дозволяє router'у розбудити
        підписників."""
        self._close_handler = handler

    async def connect(self) -> None:
        """Ідемпотентний — no-op якщо WS уже піднятий."""
        if self._explicit_close:
            raise RuntimeError("AppServerClient was closed")
        async with self._connect_lock:
            if self._ws is not None:
                return
            headers = {"Authorization": f"Bearer {self._auth_token}"} if self._auth_token else None
            log.info(
                "app_server_connecting",
                url=self._url,
                authenticated=bool(self._auth_token),
            )
            for attempt in range(1, _CONNECT_RETRIES + 1):
                try:
                    self._ws = await websockets.connect(
                        self._url,
                        max_size=100 * 1024 * 1024,
                        additional_headers=headers,
                    )
                    break
                except (OSError, websockets.WebSocketException) as exc:
                    if attempt == _CONNECT_RETRIES:
                        raise
                    log.warning("app_server_connect_retry", attempt=attempt, error=str(exc))
                    await asyncio.sleep(_CONNECT_BACKOFF_S * attempt)
            self._reader_task = asyncio.create_task(
                self._read_loop(),
                name="codex_app_server_reader",
            )
            log.info("app_server_connected")

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
            async with asyncio.timeout(self._request_timeout):
                return await future
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

    async def close(self) -> None:
        """Final teardown. Після цього reconnect неможливий."""
        if self._explicit_close:
            return
        log.info("app_server_closing", pending=len(self._pending))
        self._explicit_close = True
        await self._teardown_transport()
        self._notify_close()
        self._fail_pending(RuntimeError("AppServerClient closed"))

    def _ensure_open(self) -> None:
        if self._explicit_close:
            raise RuntimeError("AppServerClient is closed")
        if self._ws is None:
            raise RuntimeError("AppServerClient is not connected")

    async def _send(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("AppServerClient is not connected")
        await ws.send(orjson.dumps(payload).decode())

    async def _read_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                self._dispatch(raw if isinstance(raw, str) else raw.decode("utf-8"))
        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed:
            log.info("app_server_ws_closed")
        except Exception:  # noqa: BLE001 — backstop для read-loop, must never crash silently
            log.exception("app_server_reader_error")
        finally:
            # Reader умер — транспорт втрачено, але не explicit close;
            # CodexClient побачить is_connected=False і реконектить.
            if self._ws is ws:
                self._ws = None
                self._reader_task = None
            self._notify_close()
            self._fail_pending(RuntimeError("AppServerClient connection lost"))

    async def _teardown_transport(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    def _dispatch(self, raw: str) -> None:
        try:
            message = orjson.loads(raw)
        except orjson.JSONDecodeError:
            log.warning("app_server_bad_frame", frame=raw[:200])
            return

        if "id" in message and message["id"] is not None:
            self._resolve_response(message)
        elif "method" in message:
            self._dispatch_notification(message)
        else:
            log.warning("app_server_unknown_message", keys=list(message.keys()))

    def _dispatch_notification(self, message: dict[str, Any]) -> None:
        # JSON-RPC дозволяє params: object | array | absent — нам потрібен object,
        # інше нормалізуємо у порожній dict (з'явиться у логах, не в логіці).
        raw = message.get("params")
        params = raw if isinstance(raw, dict) else {}
        turn_id = params.get("turnId")
        note = Notification(
            method=message["method"],
            params=params,
            turn_id=turn_id if isinstance(turn_id, str) else None,
        )
        handler = self._notification_handler
        if handler is None:
            log.warning("app_server_no_notification_handler", method=note.method)
            return
        try:
            handler(note)
        except Exception:  # noqa: BLE001 — handler має sync semantics, лог + continue
            log.exception("app_server_notification_handler_failed", method=note.method)

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

    def _notify_close(self) -> None:
        handler = self._close_handler
        if handler is None:
            return
        try:
            handler()
        except Exception:  # noqa: BLE001 — user-supplied callback, isolate
            log.exception("app_server_close_handler_failed")

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
