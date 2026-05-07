"""NotesService — save / get / search / list / delete."""

from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import common_pb2, notes_pb2
from app.grpc_generated.codex.v1.notes_connect import NotesService as NotesProtocol
from app.rpc._auth import require_user
from app.rpc._mappers import note_to_pb
from app.services.notes.default import note_service

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class NotesRPC(NotesProtocol):
    @override
    async def save_note(
        self,
        request: notes_pb2.SaveNoteRequest,
        ctx: RequestContext,
    ) -> notes_pb2.Note:
        await require_user(ctx)
        note_id = request.id if request.HasField("id") else None
        async with SessionLocal() as db:
            try:
                note = await note_service.upsert(
                    db,
                    note_id=note_id,
                    title=request.title,
                    body=request.body,
                    tags=list(request.tags),
                )
            except LookupError as exc:
                raise ConnectError(Code.NOT_FOUND, str(exc)) from exc
            await db.commit()
            await db.refresh(note)
        return note_to_pb(note)

    @override
    async def get_note(
        self,
        request: notes_pb2.GetNoteRequest,
        ctx: RequestContext,
    ) -> notes_pb2.Note:
        await require_user(ctx)
        async with SessionLocal() as db:
            note = await note_service.get(db, request.id)
        if note is None:
            raise ConnectError(Code.NOT_FOUND, f"note {request.id} not found")
        return note_to_pb(note)

    @override
    async def search_notes(
        self,
        request: notes_pb2.SearchNotesRequest,
        ctx: RequestContext,
    ) -> notes_pb2.SearchNotesResponse:
        await require_user(ctx)
        limit, offset = _resolve_page(request.pagination)
        async with SessionLocal() as db:
            results = await note_service.search(
                db,
                query=request.query,
                tags=list(request.tags) or None,
                limit=limit,
                offset=offset,
            )
        return notes_pb2.SearchNotesResponse(
            hits=[notes_pb2.NoteHit(note=note_to_pb(n), rank=r) for n, r in results],
        )

    @override
    async def list_notes(
        self,
        request: notes_pb2.ListNotesRequest,
        ctx: RequestContext,
    ) -> notes_pb2.ListNotesResponse:
        await require_user(ctx)
        limit, offset = _resolve_page(request.pagination)
        async with SessionLocal() as db:
            notes = await note_service.list(
                db,
                tags=list(request.tags) or None,
                limit=limit,
                offset=offset,
            )
        return notes_pb2.ListNotesResponse(notes=[note_to_pb(n) for n in notes])

    @override
    async def delete_note(
        self,
        request: notes_pb2.DeleteNoteRequest,
        ctx: RequestContext,
    ) -> common_pb2.Empty:
        await require_user(ctx)
        async with SessionLocal() as db:
            removed = await note_service.delete(db, request.id)
            if not removed:
                raise ConnectError(Code.NOT_FOUND, f"note {request.id} not found")
            await db.commit()
        return common_pb2.Empty()


def _resolve_page(p: common_pb2.Pagination) -> tuple[int, int]:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT), max(p.offset, 0)
