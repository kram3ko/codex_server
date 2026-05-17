"""Notes CRUD + full-text search через Postgres tsvector + GIN.

Per-user scoping: усі методи беруть `user_id` і фільтрують по owner'у.
Cross-user sharing не передбачено — get/delete повертають None/False для
чужого `note_id` (не 403 щоб не leak'ати існування).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models import Note


class NoteService:
    async def get(
        self,
        session: AsyncSession,
        note_id: int,
        *,
        user_id: int,
    ) -> Note | None:
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id)
        return (await session.execute(stmt)).scalar_one_or_none()

    async def upsert(
        self,
        session: AsyncSession,
        *,
        note_id: int | None,
        user_id: int,
        title: str,
        body: str,
        tags: list[str],
    ) -> Note:
        if note_id is None:
            note = Note(user_id=user_id, title=title, body=body, tags=tags)
            session.add(note)
            await session.flush()
            return note
        note = await self.get(session, note_id, user_id=user_id)
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
        user_id: int,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Note]:
        stmt = (
            select(Note)
            .where(Note.user_id == user_id)
            .order_by(Note.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if tags:
            stmt = stmt.where(Note.tags.contains(tags))
        rows = await session.execute(stmt)
        return list(rows.scalars())

    async def search(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        query: str,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Note, float]]:
        """Returns (Note, ts_rank) ordered by rank desc, filtered by owner."""
        tsq = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank(Note.search_vector, tsq).label("rank")
        stmt = (
            select(Note, rank)
            .where(Note.user_id == user_id, Note.search_vector.op("@@")(tsq))
            .order_by(rank.desc())
            .limit(limit)
            .offset(offset)
        )
        if tags:
            stmt = stmt.where(Note.tags.contains(tags))
        rows = await session.execute(stmt)
        return [(row[0], float(row[1])) for row in rows.all()]

    async def delete(
        self,
        session: AsyncSession,
        note_id: int,
        *,
        user_id: int,
    ) -> bool:
        note = await self.get(session, note_id, user_id=user_id)
        if note is None:
            return False
        await session.delete(note)
        return True
