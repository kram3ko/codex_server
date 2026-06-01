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

class UserRolePb(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    USER_ROLE_UNSPECIFIED: _ClassVar[UserRolePb]
    USER_ROLE_USER: _ClassVar[UserRolePb]
    USER_ROLE_ADMIN: _ClassVar[UserRolePb]
USER_ROLE_UNSPECIFIED: UserRolePb
USER_ROLE_USER: UserRolePb
USER_ROLE_ADMIN: UserRolePb

class Invite(_message.Message):
    __slots__ = ("id", "token", "created_by_user_id", "used_by_user_id", "expires_at", "used_at", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    CREATED_BY_USER_ID_FIELD_NUMBER: _ClassVar[int]
    USED_BY_USER_ID_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_FIELD_NUMBER: _ClassVar[int]
    USED_AT_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    token: str
    created_by_user_id: int
    used_by_user_id: int
    expires_at: _timestamp_pb2.Timestamp
    used_at: _timestamp_pb2.Timestamp
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[int] = ..., token: _Optional[str] = ..., created_by_user_id: _Optional[int] = ..., used_by_user_id: _Optional[int] = ..., expires_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., used_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AdminUser(_message.Message):
    __slots__ = ("id", "email", "tg_user_id", "display_name", "role", "created_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    TG_USER_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: int
    email: str
    tg_user_id: int
    display_name: str
    role: UserRolePb
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[int] = ..., email: _Optional[str] = ..., tg_user_id: _Optional[int] = ..., display_name: _Optional[str] = ..., role: _Optional[_Union[UserRolePb, str]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class CreateInviteRequest(_message.Message):
    __slots__ = ("ttl_days",)
    TTL_DAYS_FIELD_NUMBER: _ClassVar[int]
    ttl_days: int
    def __init__(self, ttl_days: _Optional[int] = ...) -> None: ...

class CreateInviteResponse(_message.Message):
    __slots__ = ("invite",)
    INVITE_FIELD_NUMBER: _ClassVar[int]
    invite: Invite
    def __init__(self, invite: _Optional[_Union[Invite, _Mapping]] = ...) -> None: ...

class ListInvitesRequest(_message.Message):
    __slots__ = ("include_used",)
    INCLUDE_USED_FIELD_NUMBER: _ClassVar[int]
    include_used: bool
    def __init__(self, include_used: bool = ...) -> None: ...

class ListInvitesResponse(_message.Message):
    __slots__ = ("invites",)
    INVITES_FIELD_NUMBER: _ClassVar[int]
    invites: _containers.RepeatedCompositeFieldContainer[Invite]
    def __init__(self, invites: _Optional[_Iterable[_Union[Invite, _Mapping]]] = ...) -> None: ...

class RevokeInviteRequest(_message.Message):
    __slots__ = ("invite_id",)
    INVITE_ID_FIELD_NUMBER: _ClassVar[int]
    invite_id: int
    def __init__(self, invite_id: _Optional[int] = ...) -> None: ...

class CreateUserRequest(_message.Message):
    __slots__ = ("email", "password", "display_name", "role")
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    PASSWORD_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    email: str
    password: str
    display_name: str
    role: UserRolePb
    def __init__(self, email: _Optional[str] = ..., password: _Optional[str] = ..., display_name: _Optional[str] = ..., role: _Optional[_Union[UserRolePb, str]] = ...) -> None: ...

class ListUsersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListUsersResponse(_message.Message):
    __slots__ = ("users",)
    USERS_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[AdminUser]
    def __init__(self, users: _Optional[_Iterable[_Union[AdminUser, _Mapping]]] = ...) -> None: ...

class SshKey(_message.Message):
    __slots__ = ("name", "host", "user", "created_at")
    NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    name: str
    host: str
    user: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, name: _Optional[str] = ..., host: _Optional[str] = ..., user: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AddSshKeyRequest(_message.Message):
    __slots__ = ("name", "host", "user", "private_key")
    NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    PRIVATE_KEY_FIELD_NUMBER: _ClassVar[int]
    name: str
    host: str
    user: str
    private_key: str
    def __init__(self, name: _Optional[str] = ..., host: _Optional[str] = ..., user: _Optional[str] = ..., private_key: _Optional[str] = ...) -> None: ...

class ListSshKeysRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListSshKeysResponse(_message.Message):
    __slots__ = ("keys",)
    KEYS_FIELD_NUMBER: _ClassVar[int]
    keys: _containers.RepeatedCompositeFieldContainer[SshKey]
    def __init__(self, keys: _Optional[_Iterable[_Union[SshKey, _Mapping]]] = ...) -> None: ...

class DeleteSshKeyRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class ApiToken(_message.Message):
    __slots__ = ("name", "host", "user", "created_at")
    NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    name: str
    host: str
    user: str
    created_at: _timestamp_pb2.Timestamp
    def __init__(self, name: _Optional[str] = ..., host: _Optional[str] = ..., user: _Optional[str] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class AddApiTokenRequest(_message.Message):
    __slots__ = ("name", "host", "user", "token")
    NAME_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    name: str
    host: str
    user: str
    token: str
    def __init__(self, name: _Optional[str] = ..., host: _Optional[str] = ..., user: _Optional[str] = ..., token: _Optional[str] = ...) -> None: ...

class ListApiTokensRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListApiTokensResponse(_message.Message):
    __slots__ = ("tokens",)
    TOKENS_FIELD_NUMBER: _ClassVar[int]
    tokens: _containers.RepeatedCompositeFieldContainer[ApiToken]
    def __init__(self, tokens: _Optional[_Iterable[_Union[ApiToken, _Mapping]]] = ...) -> None: ...

class DeleteApiTokenRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...
