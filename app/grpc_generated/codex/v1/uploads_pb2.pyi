import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from codex.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Upload(_message.Message):
    __slots__ = ("id", "chat_id", "filename", "mime", "size", "s3_path", "extracted_text", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    MIME_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    S3_PATH_FIELD_NUMBER: _ClassVar[int]
    EXTRACTED_TEXT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    chat_id: int
    filename: str
    mime: str
    size: int
    s3_path: str
    extracted_text: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[int] = ..., chat_id: _Optional[int] = ..., filename: _Optional[str] = ..., mime: _Optional[str] = ..., size: _Optional[int] = ..., s3_path: _Optional[str] = ..., extracted_text: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class UploadOnceRequest(_message.Message):
    __slots__ = ("chat_id", "filename", "mime", "data")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    MIME_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    filename: str
    mime: str
    data: bytes
    def __init__(self, chat_id: _Optional[int] = ..., filename: _Optional[str] = ..., mime: _Optional[str] = ..., data: _Optional[bytes] = ...) -> None: ...

class UploadResponse(_message.Message):
    __slots__ = ("upload", "presigned_url", "presigned_ttl_seconds")
    UPLOAD_FIELD_NUMBER: _ClassVar[int]
    PRESIGNED_URL_FIELD_NUMBER: _ClassVar[int]
    PRESIGNED_TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    upload: Upload
    presigned_url: str
    presigned_ttl_seconds: int
    def __init__(self, upload: _Optional[_Union[Upload, _Mapping]] = ..., presigned_url: _Optional[str] = ..., presigned_ttl_seconds: _Optional[int] = ...) -> None: ...

class GetPresignedRequest(_message.Message):
    __slots__ = ("upload_id", "source", "ttl_seconds")
    UPLOAD_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    TTL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    upload_id: int
    source: str
    ttl_seconds: int
    def __init__(self, upload_id: _Optional[int] = ..., source: _Optional[str] = ..., ttl_seconds: _Optional[int] = ...) -> None: ...

class GetPresignedResponse(_message.Message):
    __slots__ = ("url", "expires_at")
    URL_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    url: str
    expires_at: _timestamp_pb2.Timestamp
    def __init__(self, url: _Optional[str] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListUploadsRequest(_message.Message):
    __slots__ = ("chat_id", "pagination")
    CHAT_ID_FIELD_NUMBER: _ClassVar[int]
    PAGINATION_FIELD_NUMBER: _ClassVar[int]
    chat_id: int
    pagination: _common_pb2.Pagination
    def __init__(self, chat_id: _Optional[int] = ..., pagination: _Optional[_Union[_common_pb2.Pagination, _Mapping]] = ...) -> None: ...

class ListUploadsResponse(_message.Message):
    __slots__ = ("uploads",)
    UPLOADS_FIELD_NUMBER: _ClassVar[int]
    uploads: _containers.RepeatedCompositeFieldContainer[Upload]
    def __init__(self, uploads: _Optional[_Iterable[_Union[Upload, _Mapping]]] = ...) -> None: ...

class DeleteUploadRequest(_message.Message):
    __slots__ = ("upload_id",)
    UPLOAD_ID_FIELD_NUMBER: _ClassVar[int]
    upload_id: int
    def __init__(self, upload_id: _Optional[int] = ...) -> None: ...

class TranscribeUploadRequest(_message.Message):
    __slots__ = ("upload_id",)
    UPLOAD_ID_FIELD_NUMBER: _ClassVar[int]
    upload_id: int
    def __init__(self, upload_id: _Optional[int] = ...) -> None: ...

class TranscribeUploadResponse(_message.Message):
    __slots__ = ("text",)
    TEXT_FIELD_NUMBER: _ClassVar[int]
    text: str
    def __init__(self, text: _Optional[str] = ...) -> None: ...
