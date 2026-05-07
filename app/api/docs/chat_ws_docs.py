"""OpenAPI опис для WebSocket /chat/ws.

WebSocket-routes у Swagger UI рендеряться скромно (тільки що вони є);
повний wire-format живе тут і у docstring `chat_ws_handler`.
"""

CHAT_WS_SUMMARY = "Chat WebSocket — bidi-стрім токенів від Codex"

CHAT_WS_DESCRIPTION = """
Bidirectional WS-стрім між браузером і Codex CLI sidecar. Один WS-конект =
одна Codex-сесія, тримається до закриття браузером або обриву мережі.
При обриві Codex-thread персистнутий у БД (`chats.codex_thread_id`) —
наступний conn'ект ту ж розмову підхопить.

### Auth

JWT через `?token=...` (браузерні WS не дають custom headers). Видається
через Connect-RPC `AuthService.Login`.

### Wire format

**Клієнт → сервер:**

```json
{"type": "user_message", "text": "...", "chat_id": int|null,
 "attachment_ids": [int]}
{"type": "interrupt"}
```

**Сервер → клієнт:**

```json
{"type": "ready"}
{"type": "chat", "chat_id": int}
{"type": "token", "delta": "..."}
{"type": "tool_call", "name": "...", "args": {...}}
{"type": "tool_result", "name": "...", "result": "...", "error": null}
{"type": "done", "chat_id": int, "final_text": "..."}
{"type": "error", "code": "...", "detail": "..."}
```

### Persist

Кожен turn пише user-message + assistant-message + події (TURN_STARTED,
TURN_COMPLETED, TURN_FAILED) у Postgres.
"""
