"""Structured tool error → 422 with stable error code (handled у core.py)."""


class ToolError(Exception):
    def __init__(self, message: str, *, code: str = "tool_error") -> None:
        super().__init__(message)
        self.code = code
