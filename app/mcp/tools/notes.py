"""note_save / note_search / note_list — per-user memory для Codex.

Convention: codex-saved notes завжди отримують тег `codex:auto` — UI/user
може фільтрувати, відрізнити від ручних нотаток. Search/list бачать ВСІ
notes власника (FK user_id), щоб codex міг читати і ручкою писані
guidelines/преференції, не лише свої.
"""

from typing import Annotated

from fastmcp.exceptions import ToolError
from pydantic import Field

from app.db.base import SessionLocal
from app.mcp.authz import require_identity
from app.mcp.core import mcp
from app.mcp.schemas.notes import NoteHit, NoteRef
from app.services.notes.default import note_service

_CODEX_TAG = "codex:auto"


@mcp.tool(name="note_save")
async def note_save(
    *,
    title: Annotated[
        str,
        Field(
            min_length=1,
            max_length=255,
            description="Короткий ключ (snake_case): `code_style`, `git_workflow`, `paths`, ...",
        ),
    ],
    body: Annotated[
        str,
        Field(min_length=1, description="Сам факт — 1-3 речення, дослівно як треба пам'ятати."),
    ],
    tags: Annotated[
        list[str] | None,
        Field(description="Додаткові теги (опц.). `codex:auto` додається автоматично."),
    ] = None,
    note_id: Annotated[
        int | None,
        Field(description="Id існуючої нотатки для оновлення; пропусти щоб створити нову."),
    ] = None,
) -> NoteRef:
    """Save a user preference / fact / context to long-term memory.

    Call when user explicitly says "remember X" or you noticed a recurring
    preference worth persisting. Before updating an existing fact —
    `note_search` to get its id, then pass via `note_id`.
    """
    claims = await require_identity()
    final_tags: list[str] = sorted({*(tags or []), _CODEX_TAG})
    async with SessionLocal() as db:
        try:
            note = await note_service.upsert(
                db,
                note_id=note_id,
                user_id=claims.user_id,
                title=title,
                body=body,
                tags=final_tags,
            )
        except LookupError as exc:
            raise ToolError(str(exc)) from exc
        await db.commit()
        await db.refresh(note)
    return NoteRef(
        id=note.id,
        title=note.title,
        tags=list(note.tags),
        updated_at=note.updated_at.isoformat(),
    )


@mcp.tool(name="note_search")
async def note_search(
    *,
    query: Annotated[
        str,
        Field(min_length=1, description="Free-text query (Postgres `websearch_to_tsquery`)."),
    ],
    tags: Annotated[
        list[str] | None,
        Field(description="Filter — повертати тільки notes що містять ВСІ ці tags."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Max hits.")] = 10,
) -> list[NoteHit]:
    """FTS-search user's notes (codex-saved + user-typed), newest-relevant first.

    Use at conversation start to recall context, or whenever you need a
    preference you might have saved earlier. Results include `id` — pass it
    into `note_save(note_id=...)` to update.
    """
    claims = await require_identity()
    async with SessionLocal() as db:
        results = await note_service.search(
            db,
            user_id=claims.user_id,
            query=query,
            tags=tags,
            limit=limit,
        )
    return [
        NoteHit(
            id=note.id,
            title=note.title,
            body=note.body,
            tags=list(note.tags),
            updated_at=note.updated_at.isoformat(),
            rank=rank,
        )
        for note, rank in results
    ]


@mcp.tool(name="note_list")
async def note_list(
    *,
    tags: Annotated[
        list[str] | None,
        Field(description="Filter — повертати тільки notes що містять ВСІ ці tags."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="Max items.")] = 50,
) -> list[NoteHit]:
    """List user notes newest-first, optionally filtered by tags.

    Use without query when you want a snapshot of preferences
    (`tags=["codex:auto"]`) or full user-notes dump.
    """
    claims = await require_identity()
    async with SessionLocal() as db:
        notes = await note_service.list(
            db,
            user_id=claims.user_id,
            tags=tags,
            limit=limit,
        )
    return [
        NoteHit(
            id=note.id,
            title=note.title,
            body=note.body,
            tags=list(note.tags),
            updated_at=note.updated_at.isoformat(),
        )
        for note in notes
    ]
