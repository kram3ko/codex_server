import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from codex.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ChatSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHAT_SOURCE_UNSPECIFIED: _ClassVar[ChatSource]
    CHAT_SOURCE_WEB: _ClassVar[ChatSource]
    CHAT_SOURCE_TELEGRAM: _ClassVar[ChatSource]
CHAT_SOURCE_UNSPECIFIED: ChatSource
CHAT_SOURCE_WEB: ChatSource
CHAT_SOURCE_TELEGRAM: ChatSource

class Chat(_message.Message):
    __slots__ = ("id", "user_id", "source", "tg_chat_id", "codex_thread_id", "title", "created_at", "last_msg_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    TG_CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    CODEX_THREAD_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    LAST_MSG_AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    user_id: int
    source: ChatSource
    tg_chat_id: int
    codex_thread_id: str
    title: str
    created_at: _timestamp_pb2.Timestamp
    last_msg_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[int] = ..., user_id: _Optional[int] = ..., source: _Optional[_Union[ChatSource, str]] = ..., tg_chat_id: _Optional[int] = ..., codex_thread_id: _Optional[str] = ..., title: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., last_msg_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListChatsRequest(_message.Message):
    __slots__ = ("pagination",)
    PAGINATION_FIELD_NUMBER: _ClassVar[int]
    pagination: _common_pb2.Pagination
    def __init__(self, pagination: _Optional[_Union[_common_pb2.Pagination, _Mapping]] = ...) -> None: ...

class ListChatsResponse(_message.Message):
    __slots__ = ("chats",)
    CHATS_FIELD_NUMBER: _ClassVar[int]
    chats: _containers.RepeatedCompositeFieldContainer[Chat]
    def __init__(self, chats: _Optional[_Iterable[_Union[Chat, _Mapping]]] = ...) -> None: ...

class GetChatRequest(_message.Message):
    __slots__ = ("chat_id",)
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    def __init__(self, chat_id: _Optional[int] = ...) -> None: ...

class RenameChatRequest(_message.Message):
    __slots__ = ("chat_id", "title")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    title: str
    def __init__(self, chat_id: _Optional[int] = ..., title: _Optional[str] = ...) -> None: ...

class DeleteChatRequest(_message.Message):
    __slots__ = ("chat_id",)
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    def __init__(self, chat_id: _Optional[int] = ...) -> None: ...
