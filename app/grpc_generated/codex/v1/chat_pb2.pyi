import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf import struct_pb2 as _struct_pb2
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

class RunTurnRequest(_message.Message):
    __slots__ = ("chat_id", "text", "upload_ids")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    UPLOAD_IDS_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    text: str
    upload_ids: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, chat_id: _Optional[int] = ..., text: _Optional[str] = ..., upload_ids: _Optional[_Iterable[int]] = ...) -> None: ...

class ChatEvent(_message.Message):
    __slots__ = ("token", "tool_call", "tool_result", "done", "error")
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    DONE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    token: TokenEvent
    tool_call: ToolCallEvent
    tool_result: ToolResultEvent
    done: DoneEvent
    error: ErrorEvent
    def __init__(self, token: _Optional[_Union[TokenEvent, _Mapping]] = ..., tool_call: _Optional[_Union[ToolCallEvent, _Mapping]] = ..., tool_result: _Optional[_Union[ToolResultEvent, _Mapping]] = ..., done: _Optional[_Union[DoneEvent, _Mapping]] = ..., error: _Optional[_Union[ErrorEvent, _Mapping]] = ...) -> None: ...

class TokenEvent(_message.Message):
    __slots__ = ("delta",)
    DELTA_FIELD_NUMBER: _ClassVar[int]
    delta: str
    def __init__(self, delta: _Optional[str] = ...) -> None: ...

class ToolCallEvent(_message.Message):
    __slots__ = ("name", "args")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    name: str
    args: _struct_pb2.Struct
    def __init__(self, name: _Optional[str] = ..., args: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ...) -> None: ...

class Attachment(_message.Message):
    __slots__ = ("kind", "source", "caption")
    KIND_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CAPTION_FIELD_NUMBER: _ClassVar[int]
    kind: str
    source: str
    caption: str
    def __init__(self, kind: _Optional[str] = ..., source: _Optional[str] = ..., caption: _Optional[str] = ...) -> None: ...

class ToolResultEvent(_message.Message):
    __slots__ = ("name", "text", "attachments", "error")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    ATTACHMENTS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    name: str
    text: str
    attachments: _containers.RepeatedCompositeFieldContainer[Attachment]
    error: str
    def __init__(self, name: _Optional[str] = ..., text: _Optional[str] = ..., attachments: _Optional[_Iterable[_Union[Attachment, _Mapping]]] = ..., error: _Optional[str] = ...) -> None: ...

class DoneEvent(_message.Message):
    __slots__ = ("chat_id", "final_text")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    FINAL_TEXT_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    final_text: str
    def __init__(self, chat_id: _Optional[int] = ..., final_text: _Optional[str] = ...) -> None: ...

class ErrorEvent(_message.Message):
    __slots__ = ("code", "detail")
    CODE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    code: str
    detail: str
    def __init__(self, code: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class InterruptTurnRequest(_message.Message):
    __slots__ = ("chat_id",)
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    def __init__(self, chat_id: _Optional[int] = ...) -> None: ...

class InterruptTurnResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class SteerTurnRequest(_message.Message):
    __slots__ = ("chat_id", "text")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    text: str
    def __init__(self, chat_id: _Optional[int] = ..., text: _Optional[str] = ...) -> None: ...

class SteerTurnResponse(_message.Message):
    __slots__ = ("accepted",)
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    def __init__(self, accepted: bool = ...) -> None: ...

class GetCodexUsageRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class UsageWindow(_message.Message):
    __slots__ = ("used_percent", "window_minutes", "resets_at")
    USED_PERCENT_FIELD_NUMBER: _ClassVar[int]
    WINDOW_MINUTES_FIELD_NUMBER: _ClassVar[int]
    RESETS_AT_FIELD_NUMBER: _ClassVar[int]
    used_percent: float
    window_minutes: int
    resets_at: _timestamp_pb2.Timestamp
    def __init__(self, used_percent: _Optional[float] = ..., window_minutes: _Optional[int] = ..., resets_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class CodexUsage(_message.Message):
    __slots__ = ("plan_type", "primary", "secondary")
    PLAN_TYPE_FIELD_NUMBER: _ClassVar[int]
    PRIMARY_FIELD_NUMBER: _ClassVar[int]
    SECONDARY_FIELD_NUMBER: _ClassVar[int]
    plan_type: str
    primary: UsageWindow
    secondary: UsageWindow
    def __init__(self, plan_type: _Optional[str] = ..., primary: _Optional[_Union[UsageWindow, _Mapping]] = ..., secondary: _Optional[_Union[UsageWindow, _Mapping]] = ...) -> None: ...
