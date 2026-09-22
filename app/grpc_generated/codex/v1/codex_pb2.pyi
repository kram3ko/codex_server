from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ReasoningEffortOption(_message.Message):
    __slots__ = ("value", "description")
    VALUE_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    value: str
    description: str
    def __init__(self, value: _Optional[str] = ..., description: _Optional[str] = ...) -> None: ...

class CodexModel(_message.Message):
    __slots__ = ("id", "display_name", "description", "is_default", "default_reasoning_effort", "supported_reasoning_efforts")
    ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    IS_DEFAULT_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_REASONING_EFFORT_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_REASONING_EFFORTS_FIELD_NUMBER: _ClassVar[int]
    id: str
    display_name: str
    description: str
    is_default: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: _containers.RepeatedCompositeFieldContainer[ReasoningEffortOption]
    def __init__(self, id: _Optional[str] = ..., display_name: _Optional[str] = ..., description: _Optional[str] = ..., is_default: bool = ..., default_reasoning_effort: _Optional[str] = ..., supported_reasoning_efforts: _Optional[_Iterable[_Union[ReasoningEffortOption, _Mapping]]] = ...) -> None: ...

class ListModelsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListModelsResponse(_message.Message):
    __slots__ = ("models", "fallback_reasoning_effort")
    MODELS_FIELD_NUMBER: _ClassVar[int]
    FALLBACK_REASONING_EFFORT_FIELD_NUMBER: _ClassVar[int]
    models: _containers.RepeatedCompositeFieldContainer[CodexModel]
    fallback_reasoning_effort: str
    def __init__(self, models: _Optional[_Iterable[_Union[CodexModel, _Mapping]]] = ..., fallback_reasoning_effort: _Optional[str] = ...) -> None: ...

class CodexPreferences(_message.Message):
    __slots__ = ("model", "reasoning_effort")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    REASONING_EFFORT_FIELD_NUMBER: _ClassVar[int]
    model: str
    reasoning_effort: str
    def __init__(self, model: _Optional[str] = ..., reasoning_effort: _Optional[str] = ...) -> None: ...

class GetPreferencesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class UpdatePreferencesRequest(_message.Message):
    __slots__ = ("model", "reasoning_effort")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    REASONING_EFFORT_FIELD_NUMBER: _ClassVar[int]
    model: str
    reasoning_effort: str
    def __init__(self, model: _Optional[str] = ..., reasoning_effort: _Optional[str] = ...) -> None: ...
