"""MessageService — list messages of a chat."""

from typing import override

from connectrpc.code import Code
from connectrpc.errors import ConnectError
from connectrpc.request import RequestContext

from app.db.base import SessionLocal
from app.grpc_generated.codex.v1 import common_pb2, message_pb2
from app.grpc_generated.codex.v1.message_connect import MessageService as MessageProtocol
from app.rpc._auth import require_user
from app.rpc._mappers import message_to_pb
from app.services.chats.default import chat_service
from app.services.messages.default import message_service

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


class MessageRPC(MessageProtocol):
    @override
    async def list_messages(
        self,
        request: message_pb2.ListMessagesRequest,
        ctx: RequestContext,
    ) -> message_pb2.ListMessagesResponse:
        user = await require_user(ctx)
        limit = _resolve_limit(request.pagination)
        async with SessionLocal() as db:
            chat = await chat_service.get(db, request.chat_id)
            if chat is None or chat.user_id != user.id:
                raise ConnectError(Code.NOT_FOUND, f"chat {request.chat_id} not found")
            messages = await message_service.list(db, request.chat_id, limit=limit)
        return message_pb2.ListMessagesResponse(messages=[message_to_pb(m) for m in messages])


def _resolve_limit(p: common_pb2.Pagination) -> int:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)
