"""Стабільні machine-readable коди для `ErrorEvent.code`.

Wire-contract: frontend бачить ці рядки у `chat_pb2.ErrorEvent.code`. Зміна
значень = breaking change. Додаємо нові, ніколи не перейменовуємо існуючі.
"""

from enum import StrEnum


class CodexErrorCode(StrEnum):
    EMPTY_TEXT = "empty_text"
    EMPTY_RESPONSE = "empty_response"
    CODEX_ERROR = "codex_error"
    TURN_TIMEOUT = "turn_timeout"
    TURN_BUSY = "turn_busy"
    STALE_ACTIVE_TURN = "stale_active_turn"
    STREAM_DROPPED = "stream_dropped"
    RATE_LIMITED = "rate_limited"
