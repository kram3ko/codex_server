"""Sentry `before_send` фільтр.

Знімає чутливе з request/breadcrumb payload'ів до того як event ляже у Bugsink.
LLM пізніше читає це через MCP `get_error` — будь-який витік (Authorization,
user prompts у breadcrumb'ах) бачить codex-cli sidecar.
"""

from typing import Any

_REDACTED = "[redacted]"
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "proxy-authorization",
    }
)


def scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_headers(request.get("headers"))
        _scrub_headers(request.get("cookies"))
        if "data" in request:
            request["data"] = _REDACTED
        env = request.get("env")
        if isinstance(env, dict):
            _scrub_headers(env)

    for breadcrumb in event.get("breadcrumbs", {}).get("values", []) or []:
        if not isinstance(breadcrumb, dict):
            continue
        data = breadcrumb.get("data")
        if isinstance(data, dict):
            _scrub_headers(data)
    return event


def _scrub_headers(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    for key in list(payload.keys()):
        if str(key).lower() in _SENSITIVE_HEADERS:
            payload[key] = _REDACTED
