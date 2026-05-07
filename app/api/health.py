"""HTTP /health — мінімальний liveness для docker/oncall."""

from fastapi import APIRouter

from app.api.docs.health_docs import (
    HEALTH_DESCRIPTION,
    HEALTH_RESPONSES,
    HEALTH_SUMMARY,
)

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    include_in_schema=False,
    summary=HEALTH_SUMMARY,
    description=HEALTH_DESCRIPTION,
    responses=HEALTH_RESPONSES,
)
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "codex-api"}
