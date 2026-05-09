"""Тонкий JSON-RPC 2.0 клієнт поверх `codex app-server` WebSocket.

Реалізує мінімум потрібний для одного chat-турну: initialize → thread/start →
turn/start → стрім notifications → close. Pending requests track'аться по
числовому id; notifications кладуться в `asyncio.Queue` для consumer'a.

Transport-level reconnect: якщо WS падає сам по собі (мережа, таймаут),
`is_connected` стає False і пендінги дофейлюються; явним `connect()` ззовні
сесія піднімається наново. Стан thread'у — відповідальність CodexClient.
Lifecycle: `connect()` ідемпотентний; `close()` — final, після нього
жоден `connect()` не дозволений.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import orjson
import structlog
import websockets

log = structlog.get_logger(__name__)

_JSONRPC_VERSION = "2.0"
_DEFAULT_REQUEST_TIMEOUT = 60.0
_NOTIFICATION_QUEUE_MAX = 1000
# Sidecar може transient'но не resolv'итись (recreate, DNS прогрів) одразу
# після свого старту. Кілька коротких retry поглинають це вікно.
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
        self._ws: websockets.ClientConnection | None = None
        self._reader_task: asyncio.Task | None = None
        self._explicit_close = False
        self._connect_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._explicit_close

    async def connect(self) -> None:
        """Ідемпотентний — no-op якщо WS вже є; піднімає новий якщо ні.

        Кидає RuntimeError якщо клієнт уже був явно закритий.
        """
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

    async def notifications(self) -> AsyncIterator[Notification]:
        while True:
            note = await self._notifications.get()
            if note is None:
                return
            yield note

    async def close(self) -> None:
        """Final teardown. Після цього reconnect неможливий."""
        if self._explicit_close:
            return
        log.info("app_server_closing", pending=len(self._pending))
        self._explicit_close = True
        await self._teardown_transport()
        self._wake_notifications()
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
        # Codex sidecar приймає тільки text-frame'и: orjson → bytes → decode.
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
        except Exception as exc:  # noqa: BLE001 — логнути будь-що і завершитись
            log.error("app_server_reader_error", error=str(exc))
        finally:
            # Reader умер — транспорт втрачено, але це не explicit close;
            # CodexClient побачить is_connected=False і реконектить.
            if self._ws is ws:
                self._ws = None
                self._reader_task = None
            self._wake_notifications()
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
            note = Notification(
                method=message["method"],
                params=message.get("params") or {},
            )
            try:
                self._notifications.put_nowait(note)
            except asyncio.QueueFull:
                log.warning(
                    "app_server_notification_dropped",
                    method=note.method,
                    queue_size=self._notifications.qsize(),
                )
        else:
            log.warning("app_server_unknown_message", keys=list(message.keys()))

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

    def _wake_notifications(self) -> None:
        try:
            self._notifications.put_nowait(None)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._notifications.get_nowait()
            self._notifications.put_nowait(None)

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
