"""EventService — observability journal listing."""

from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext
from sqlalchemy import select

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import common_pb2, event_pb2
from app.grpc_generated.codex.v1.event_connect import EventService as EventProtocol
from app.models import Chat, Event
from app.rpc._auth import require_user
from app.rpc._mappers import event_kind_from_pb, event_to_pb

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


class EventRPC(EventProtocol):
    @override
    async def list_events(
        self,
        request: event_pb2.ListEventsRequest,
        ctx: RequestContext,
    ) -> event_pb2.ListEventsResponse:
        user = await require_user(ctx)
        limit = _resolve_limit(request.pagination)
        offset = max(request.pagination.offset, 0)

        stmt = (
            select(Event)
            .where((Event.user_id == user.id) | (Event.user_id.is_(None)))
            .order_by(Event.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if request.HasField("chat_id"):
            await _ensure_chat_ownership(request.chat_id, user.id)
            stmt = stmt.where(Event.chat_id == request.chat_id)
        if request.HasField("kind"):
            kind = event_kind_from_pb(request.kind)
            if kind is None:
                raise ConnectError(Code.INVALID_ARGUMENT, f"unknown event kind: {request.kind}")
            stmt = stmt.where(Event.kind == kind)

        async with SessionLocal() as db:
            rows = await db.execute(stmt)
            events = list(rows.scalars())
        return event_pb2.ListEventsResponse(events=[event_to_pb(e) for e in events])


def _resolve_limit(p: common_pb2.Pagination) -> int:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


async def _ensure_chat_ownership(chat_id: int, user_id: int) -> None:
    async with SessionLocal() as db:
        chat = await db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        raise ConnectError(Code.NOT_FOUND, f"chat {chat_id} not found")
