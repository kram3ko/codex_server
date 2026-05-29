"""ORM model → protobuf message converters.

Single source of truth for serialisation — RPC handlers don't touch
protobuf field assignment directly.
"""

from datetime import datetime
from typing import Any

from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp

from app.grpc_generated.codex.v1 import (
    chat_pb2,
    event_pb2,
    message_pb2,
    notes_pb2,
    uploads_pb2,
    user_pb2,
)
from app.models import (
    Chat,
    ChatSource,
    Event,
    EventKind,
    Message,
    MessageRole,
    Note,
    Upload,
    User,
)

_CHAT_SOURCE_PB = {s: getattr(chat_pb2, f"CHAT_SOURCE_{s.value}") for s in ChatSource}
_MESSAGE_ROLE_PB = {r: getattr(message_pb2, f"MESSAGE_ROLE_{r.value}") for r in MessageRole}
_EVENT_KIND_PB = {k: getattr(event_pb2, f"EVENT_KIND_{k.value}") for k in EventKind}

_CHAT_SOURCE_FROM_PB = {v: k for k, v in _CHAT_SOURCE_PB.items()}
_EVENT_KIND_FROM_PB = {v: k for k, v in _EVENT_KIND_PB.items()}


def to_ts(dt: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def to_struct(payload: dict[str, Any] | None) -> Struct | None:
    if payload is None:
        return None
    s = Struct()
    s.update(payload)
    return s


def user_to_pb(u: User) -> user_pb2.User:
    msg = user_pb2.User(id=u.id, role=u.role.value, created_at=to_ts(u.created_at))
    if u.tg_user_id is not None:
        msg.tg_user_id = u.tg_user_id
    if u.email is not None:
        msg.email = u.email
    if u.display_name is not None:
        msg.display_name = u.display_name
    return msg


def chat_to_pb(c: Chat, *, active_turn_id: int | None = None) -> chat_pb2.Chat:
    msg = chat_pb2.Chat(
        id=c.id,
        user_id=c.user_id,
        source=_CHAT_SOURCE_PB[c.source],
        created_at=to_ts(c.created_at),
        last_msg_at=to_ts(c.last_msg_at),
    )
    if c.tg_chat_id is not None:
        msg.tg_chat_id = c.tg_chat_id
    if c.codex_thread_id is not None:
        msg.codex_thread_id = c.codex_thread_id
    if c.title is not None:
        msg.title = c.title
    if active_turn_id is not None:
        msg.active_turn_id = active_turn_id
    return msg


def message_to_pb(m: Message) -> message_pb2.Message:
    msg = message_pb2.Message(
        id=m.id,
        chat_id=m.chat_id,
        role=_MESSAGE_ROLE_PB[m.role],
        text=m.text,
        created_at=to_ts(m.created_at),
    )
    meta = to_struct(m.meta)
    if meta is not None:
        msg.meta.CopyFrom(meta)
    return msg


def event_to_pb(e: Event) -> event_pb2.Event:
    msg = event_pb2.Event(
        id=e.id,
        kind=_EVENT_KIND_PB[e.kind],
        created_at=to_ts(e.created_at),
    )
    if e.chat_id is not None:
        msg.chat_id = e.chat_id
    if e.user_id is not None:
        msg.user_id = e.user_id
    payload = to_struct(e.payload)
    if payload is not None:
        msg.payload.CopyFrom(payload)
    return msg


def event_kind_from_pb(value: int) -> EventKind | None:
    return _EVENT_KIND_FROM_PB.get(value)


def note_to_pb(n: Note) -> notes_pb2.Note:
    return notes_pb2.Note(
        id=n.id,
        title=n.title,
        body=n.body,
        tags=list(n.tags),
        created_at=to_ts(n.created_at),
        updated_at=to_ts(n.updated_at),
    )


def upload_to_pb(u: Upload) -> uploads_pb2.Upload:
    msg = uploads_pb2.Upload(
        id=u.id,
        filename=u.filename,
        mime=u.mime,
        size=u.size,
        s3_path=u.s3_path,
        created_at=to_ts(u.created_at),
    )
    if u.chat_id is not None:
        msg.chat_id = u.chat_id
    if u.extracted_text is not None:
        msg.extracted_text = u.extracted_text
    return msg
