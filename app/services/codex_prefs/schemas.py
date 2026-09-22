from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ReasoningEffortOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str
    description: str


class CodexModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    description: str
    is_default: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[ReasoningEffortOption, ...]


class ReasoningEffortOptionIn(BaseModel):
    """Inbound `ReasoningEffortOption` from `model/list`."""

    model_config = ConfigDict(extra="ignore", alias_generator=to_camel)

    reasoning_effort: str
    description: str = ""


class CodexModelIn(BaseModel):
    """Inbound `Model` from `model/list`."""

    model_config = ConfigDict(extra="ignore", alias_generator=to_camel)

    id: str
    display_name: str
    description: str = ""
    hidden: bool = False
    is_default: bool = False
    default_reasoning_effort: str = ""
    supported_reasoning_efforts: list[ReasoningEffortOptionIn] = Field(default_factory=list)

    def to_domain(self) -> CodexModel:
        return CodexModel(
            id=self.id,
            display_name=self.display_name,
            description=self.description,
            is_default=self.is_default,
            default_reasoning_effort=self.default_reasoning_effort,
            supported_reasoning_efforts=tuple(
                ReasoningEffortOption(value=o.reasoning_effort, description=o.description)
                for o in self.supported_reasoning_efforts
            ),
        )


class CodexPreferences(BaseModel):
    """None = sidecar default for that dimension."""

    model_config = ConfigDict(frozen=True)

    model: str | None = None
    reasoning_effort: str | None = None


class TurnOptions(BaseModel):
    """Resolved per-turn overrides: what actually goes into `turn/start`."""

    model_config = ConfigDict(frozen=True)

    model: str | None = None
    reasoning_effort: str | None = None
