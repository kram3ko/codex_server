"""Async HTTP client for Bugsink REST API (`/api/canonical/0/*`).

Bearer auth (40-hex AuthToken). Cursor pagination — Bugsink повертає `next`
як повний URL у response body, ми витягуємо `?cursor=` параметр і повертаємо
як opaque рядок під ключем `_next_cursor`. Read-only — Bugsink не публікує
write API на issues станом на 2.1.3. Юзається MCP-тулами `list_errors` /
`get_error` у `app/mcp/tools/errors.py`.
"""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

_BASE = "/api/canonical/0"


class BugsinkClient:
    def __init__(self, base_url: str, auth_token: str, timeout_seconds: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=self._timeout
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def list_projects(self, cursor: str = "") -> dict[str, Any]:
        return await self._page(f"{_BASE}/projects/", cursor)

    async def list_issues(
        self,
        project: int,
        sort: str = "last_seen",
        order: str = "desc",
        cursor: str = "",
    ) -> dict[str, Any]:
        return await self._page(
            f"{_BASE}/issues/",
            cursor,
            {"project": project, "sort": sort, "order": order},
        )

    async def get_issue(self, issue_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"{_BASE}/issues/{issue_id}/")
        resp.raise_for_status()
        return resp.json()

    async def list_events(
        self,
        issue: str,
        order: str = "desc",
        cursor: str = "",
    ) -> dict[str, Any]:
        return await self._page(
            f"{_BASE}/events/",
            cursor,
            {"issue": issue, "order": order},
        )

    async def get_event(self, event_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"{_BASE}/events/{event_id}/")
        resp.raise_for_status()
        return resp.json()

    async def _page(
        self,
        url: str,
        cursor: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        q: dict[str, Any] = dict(params or {})
        if cursor:
            q["cursor"] = cursor
        resp = await self._http.get(url, params=q)
        resp.raise_for_status()
        body = resp.json()
        body["_next_cursor"] = _extract_cursor(body.get("next"))
        return body


def _extract_cursor(next_url: str | None) -> str:
    if not next_url:
        return ""
    parts = urlsplit(next_url)
    cursor_list = parse_qs(parts.query).get("cursor") or []
    return cursor_list[0] if cursor_list else ""
