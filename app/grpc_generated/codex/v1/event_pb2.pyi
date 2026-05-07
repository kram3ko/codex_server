import datetime

from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from codex.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EventKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EVENT_KIND_UNSPECIFIED: _ClassVar[EventKind]
    EVENT_KIND_THREAD_OPENED: _ClassVar[EventKind]
    EVENT_KIND_THREAD_RESET: _ClassVar[EventKind]
    EVENT_KIND_THREAD_LOST: _ClassVar[EventKind]
    EVENT_KIND_TURN_STARTED: _ClassVar[EventKind]
    EVENT_KIND_TURN_COMPLETED: _ClassVar[EventKind]
    EVENT_KIND_TURN_INTERRUPTED: _ClassVar[EventKind]
    EVENT_KIND_TURN_FAILED: _ClassVar[EventKind]
    EVENT_KIND_ATTACHMENT_RECEIVED: _ClassVar[EventKind]
    EVENT_KIND_AUDIO_TRANSCRIBED: _ClassVar[EventKind]
    EVENT_KIND_ERROR: _ClassVar[EventKind]
EVENT_KIND_UNSPECIFIED: EventKind
EVENT_KIND_THREAD_OPENED: EventKind
EVENT_KIND_THREAD_RESET: EventKind
EVENT_KIND_THREAD_LOST: EventKind
EVENT_KIND_TURN_STARTED: EventKind
EVENT_KIND_TURN_COMPLETED: EventKind
EVENT_KIND_TURN_INTERRUPTED: EventKind
EVENT_KIND_TURN_FAILED: EventKind
EVENT_KIND_ATTACHMENT_RECEIVED: EventKind
EVENT_KIND_AUDIO_TRANSCRIBED: EventKind
EVENT_KIND_ERROR: EventKind

class Event(_message.Message):
    __slots__ = ("id", "chat_id", "user_id", "kind", "payload", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    USER_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    chat_id: int
    user_id: int
    kind: EventKind
    payload: _struct_pb2.Struct
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[int] = ..., chat_id: _Optional[int] = ..., user_id: _Optional[int] = ..., kind: _Optional[_Union[EventKind, str]] = ..., payload: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListEventsRequest(_message.Message):
    __slots__ = ("chat_id", "kind", "pagination")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAGINATION_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    kind: EventKind
    pagination: _common_pb2.Pagination
    def __init__(self, chat_id: _Optional[int] = ..., kind: _Optional[_Union[EventKind, str]] = ..., pagination: _Optional[_Union[_common_pb2.Pagination, _Mapping]] = ...) -> None: ...

class ListEventsResponse(_message.Message):
    __slots__ = ("events",)
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    events: _containers.RepeatedCompositeFieldContainer[Event]
    def __init__(self, events: _Optional[_Iterable[_Union[Event, _Mapping]]] = ...) -> None: ...
