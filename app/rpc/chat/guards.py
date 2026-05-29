"""Загальні guards для ChatRPC: pagination clamp + ownership-check."""

from connectrpc.code import Code
from connectrpc.errors import ConnectError

from app.grpc_generated.codex.v1 import common_pb2
from app.models import Chat, ChatSource
from app.services.chats.default import chat_service

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def resolve_limit(p: common_pb2.Pagination) -> int:
    limit = p.limit if p.limit > 0 else _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


async def load_chat_owned(
    db,
    chat_id: int,
    user_id: int,
    expected_source: ChatSource | None = None,
) -> Chat:
    chat = await chat_service.get(db, chat_id)
    if chat is None or chat.user_id != user_id:
        raise ConnectError(Code.NOT_FOUND, f"chat {chat_id} not found")
    if expected_source is not None and chat.source is not expected_source:
        raise ConnectError(Code.NOT_FOUND, f"chat {chat_id} not found")
    return chat
