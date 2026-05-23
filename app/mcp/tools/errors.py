"""Bugsink read-through tools для Codex CLI.

Wraps `BugsinkClient` (HTTP до Bugsink REST API). Two-layer auth:
- MCP endpoint guarded by `MCP_CALLBACK_TOKEN` (sidecar-only network);
- per-call `authz` JWT forwarded з prompt-header `MCPAuthz: <token>`,
  щоб задовольнити AGENTS contract "every MCP tool body MUST include authz"
  і прив'язати call до user/role (RBAC у `verify_authz`).
Empty `BUGSINK_AUTH_TOKEN` → tool кидає зрозумілу ToolError замість 401.
"""

from typing import Annotated

import httpx
from fastmcp.exceptions import ToolError
from pydantic import Field

from app.mcp.core import mcp
from app.mcp.schemas.errors import EventDetail, IssueSummary
from app.services.errors.default import bugsink_client
from app.services.mcp_authz import McpAuthzError, verify_authz

_AUTHZ_DESCRIPTION = (
    "JWT з prompt-header `MCPAuthz: <token>` — обов'язково форвардити "
    "точне значення з останнього header line. Без нього виклик відхиляється."
)


def _require_authz(authz: str | None) -> None:
    if not authz:
        raise ToolError("authz required: forward `MCPAuthz: <jwt>` header from prompt")
    try:
        verify_authz(authz)
    except McpAuthzError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(name="list_errors")
async def list_errors(
    *,
    authz: Annotated[str | None, Field(description=_AUTHZ_DESCRIPTION)] = None,
    project_slug: Annotated[
        str | None,
        Field(description="Project slug; empty → first/only project."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max issues.")] = 20,
) -> list[IssueSummary]:
    """Recent error issues from this server's Bugsink, newest-first.

    Each item: {id, type, message, events, last_seen, resolved}. Call
    get_error(id) afterwards to drill into a specific issue's stacktrace.
    Use when user asks about crashes, exceptions, recent errors, or before
    suggesting a fix.
    """
    _require_authz(authz)
    project_id = await _resolve_project_id(project_slug)
    try:
        body = await bugsink_client.list_issues(project=project_id, sort="last_seen", order="desc")
    except httpx.HTTPError as exc:
        raise ToolError(f"bugsink_api_error: {exc!s}") from exc

    return [IssueSummary.model_validate(i) for i in body.get("results", [])[:limit]]


@mcp.tool(name="get_error")
async def get_error(
    *,
    authz: Annotated[str | None, Field(description=_AUTHZ_DESCRIPTION)] = None,
    issue_id: Annotated[str, Field(description="Issue UUID from list_errors.")],
) -> EventDetail:
    """Full detail for one error issue: latest event with stacktrace + tags.

    Returns {issue_id, event_id, timestamp, stacktrace, platform, level,
    logger, environment, release, transaction, tags}. Use after list_errors
    when drilling into a specific issue.
    """
    _require_authz(authz)
    try:
        events = await bugsink_client.list_events(issue=issue_id, order="desc")
    except httpx.HTTPError as exc:
        raise ToolError(f"bugsink_api_error: {exc!s}") from exc

    results = events.get("results", [])
    if not results:
        raise ToolError(f"no_events_for_issue: {issue_id}")

    detail = await bugsink_client.get_event(results[0]["id"])
    data = detail.get("data") or {}
    return EventDetail(
        issue_id=issue_id,
        event_id=detail["id"],
        timestamp=detail.get("timestamp"),
        stacktrace=detail.get("stacktrace_md", ""),
        platform=data.get("platform"),
        level=data.get("level"),
        logger=data.get("logger"),
        environment=data.get("environment"),
        release=data.get("release"),
        transaction=data.get("transaction"),
        tags=data.get("tags"),
    )


# Per-worker resolved-id cache: project_id у Bugsink стабільний (DB-генерований
# при першому ingest), повторно /projects/ запитувати на кожен tool-call не
# треба. Key=slug (None для дефолтного "first project"). Без TTL — invalidation
# = restart воркера.
_project_id_cache: dict[str | None, int] = {}


async def _resolve_project_id(slug: str | None) -> int:
    if (cached := _project_id_cache.get(slug)) is not None:
        return cached

    try:
        body = await bugsink_client.list_projects()
    except httpx.HTTPError as exc:
        raise ToolError(f"bugsink_api_error: {exc!s}") from exc

    projects = body.get("results", [])
    if not projects:
        raise ToolError("no_bugsink_projects_configured")

    if slug is None:
        pid = int(projects[0]["id"])
    else:
        match = next((p for p in projects if slug in (p.get("slug"), p.get("name"))), None)
        if match is None:
            raise ToolError(f"project_not_found: {slug}")
        pid = int(match["id"])

    _project_id_cache[slug] = pid
    return pid
