"""Notes CRUD + full-text search через per-user default notebooks."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models import Note, Notebook, NotebookKind

_DEFAULT_NOTEBOOK_TITLE = "Personal notes"


class NoteService:
    async def get(
        self,
        session: AsyncSession,
        note_id: int,
        *,
        user_id: int,
    ) -> Note | None:
        notebook = await self.default_notebook(session, user_id=user_id)
        stmt = select(Note).where(
            Note.id == note_id,
            Note.user_id == user_id,
            Note.notebook_id == notebook.id,
        )
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
        notebook = await self.default_notebook(session, user_id=user_id)
        if note_id is None:
            note = Note(
                user_id=user_id,
                notebook_id=notebook.id,
                title=title,
                body=body,
                tags=tags,
            )
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
        notebook = await self.default_notebook(session, user_id=user_id)
        stmt = (
            select(Note)
            .where(Note.user_id == user_id, Note.notebook_id == notebook.id)
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
        notebook = await self.default_notebook(session, user_id=user_id)
        tsq = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank(Note.search_vector, tsq).label("rank")
        stmt = (
            select(Note, rank)
            .where(
                Note.user_id == user_id,
                Note.notebook_id == notebook.id,
                Note.search_vector.op("@@")(tsq),
            )
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

    async def default_notebook(self, session: AsyncSession, *, user_id: int) -> Notebook:
        stmt = select(Notebook).where(
            Notebook.user_id == user_id,
            Notebook.kind == NotebookKind.PERSONAL,
        )
        notebook = (await session.execute(stmt)).scalar_one_or_none()
        if notebook is not None:
            return notebook
        create_stmt = (
            insert(Notebook)
            .values(
                user_id=user_id,
                title=_DEFAULT_NOTEBOOK_TITLE,
                kind=NotebookKind.PERSONAL,
            )
            .on_conflict_do_nothing(constraint="uq_notebooks_user_kind")
        )
        await session.execute(create_stmt)
        return (await session.execute(stmt)).scalar_one()
