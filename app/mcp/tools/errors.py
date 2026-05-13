"""Bugsink read-through tools для Codex CLI.

Wraps `BugsinkClient` (HTTP до Bugsink REST API). Без auth-gating: MCP
endpoint вже захищений `MCP_CALLBACK_TOKEN`, видно тільки sidecar'у. Empty
`BUGSINK_AUTH_TOKEN` → tool кидає зрозумілу ToolError замість 401.
"""

from typing import Annotated, Any

import httpx
from fastmcp.exceptions import ToolError
from pydantic import Field

from app.mcp.core import mcp
from app.services.errors.default import bugsink_client


@mcp.tool(
    description=(
        "List recent error issues from Bugsink. Use when user asks 'why is "
        "X crashing', 'what errors today', or before suggesting a fix. "
        "Returns most-recent-first by last_seen."
    ),
)
async def list_errors(
    project_slug: Annotated[
        str | None,
        Field(description="Project slug (e.g. 'codex-server'). Empty → first project."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max issues to return.")] = 20,
) -> dict[str, Any]:
    project_id = await _resolve_project_id(project_slug)
    try:
        body = await bugsink_client.list_issues(
            project=project_id, sort="last_seen", order="desc"
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"bugsink_api_error: {exc!s}") from exc

    issues = body.get("results", [])[:limit]
    return {
        "project_id": project_id,
        "count": len(issues),
        "issues": [
            {
                "id": i["id"],
                "type": i.get("calculated_type"),
                "message": i.get("calculated_value"),
                "transaction": i.get("transaction"),
                "events": i.get("stored_event_count"),
                "first_seen": i.get("first_seen"),
                "last_seen": i.get("last_seen"),
                "resolved": i.get("is_resolved"),
            }
            for i in issues
        ],
    }


@mcp.tool(
    description=(
        "Fetch full detail for one error issue: latest event with stacktrace, "
        "tags (release/environment/user), and breadcrumbs. Use after "
        "list_errors when drilling into a specific issue."
    ),
)
async def get_error(
    issue_id: Annotated[str, Field(description="Issue UUID from list_errors.")],
) -> dict[str, Any]:
    try:
        events = await bugsink_client.list_events(issue=issue_id, order="desc")
    except httpx.HTTPError as exc:
        raise ToolError(f"bugsink_api_error: {exc!s}") from exc

    results = events.get("results", [])
    if not results:
        raise ToolError(f"no_events_for_issue: {issue_id}")

    detail = await bugsink_client.get_event(results[0]["id"])
    data = detail.get("data") or {}
    # Whitelist: stacktrace + minimal context. Raw `data` несе request body,
    # headers, breadcrumbs з user-prompt'ами — LLM не повинен це бачити.
    return {
        "issue_id": issue_id,
        "event_id": detail["id"],
        "timestamp": detail.get("timestamp"),
        "stacktrace": detail.get("stacktrace_md", ""),
        "platform": data.get("platform"),
        "level": data.get("level"),
        "logger": data.get("logger"),
        "environment": data.get("environment"),
        "release": data.get("release"),
        "transaction": data.get("transaction"),
        "tags": data.get("tags"),
    }


async def _resolve_project_id(slug: str | None) -> int:
    try:
        body = await bugsink_client.list_projects()
    except httpx.HTTPError as exc:
        raise ToolError(f"bugsink_api_error: {exc!s}") from exc

    projects = body.get("results", [])
    if not projects:
        raise ToolError("no_bugsink_projects_configured")

    if slug is None:
        return int(projects[0]["id"])

    for project in projects:
        if project.get("slug") == slug or project.get("name") == slug:
            return int(project["id"])
    raise ToolError(f"project_not_found: {slug}")
