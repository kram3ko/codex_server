"""Notes CRUD + full-text search через Postgres tsvector + GIN."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models import Note


class NoteService:
    async def get(self, session: AsyncSession, note_id: int) -> Note | None:
        return await session.get(Note, note_id)

    async def upsert(
        self,
        session: AsyncSession,
        *,
        note_id: int | None,
        title: str,
        body: str,
        tags: list[str],
    ) -> Note:
        if note_id is None:
            note = Note(title=title, body=body, tags=tags)
            session.add(note)
            await session.flush()
            return note
        note = await session.get(Note, note_id)
        if note is None:
            raise LookupError(f"note {note_id} not found")
        note.title = title
        note.body = body
        note.tags = tags
        await session.flush()
        return note

    async def list(
        self,
        session: AsyncSession,
        *,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Note]:
        stmt = select(Note).order_by(Note.updated_at.desc()).limit(limit).offset(offset)
        if tags:
            stmt = stmt.where(Note.tags.contains(tags))
        rows = await session.execute(stmt)
        return list(rows.scalars())

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Note, float]]:
        """Returns (Note, ts_rank) ordered by rank desc."""
        tsq = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank(Note.search_vector, tsq).label("rank")
        stmt = (
            select(Note, rank)
            .where(Note.search_vector.op("@@")(tsq))
            .order_by(rank.desc())
            .limit(limit)
            .offset(offset)
        )
        if tags:
            stmt = stmt.where(Note.tags.contains(tags))
        rows = await session.execute(stmt)
        return [(row[0], float(row[1])) for row in rows.all()]

    async def delete(self, session: AsyncSession, note_id: int) -> bool:
        note = await session.get(Note, note_id)
        if note is None:
            return False
        await session.delete(note)
        return True
