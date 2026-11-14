"""HTTP shapes for optional question context; none of them authorize a customer."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class NewConversation(_ContextModel):
    mode: Literal["new"]


class ContinueConversation(_ContextModel):
    mode: Literal["continue"]
    id: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(strict=True, ge=1)


ConversationRequest = Annotated[
    NewConversation | ContinueConversation, Field(discriminator="mode")
]


class ClearConversation(_ContextModel):
    id: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class ActiveConversationReceipt(_ContextModel):
    state: Literal["active"]
    id: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(strict=True, ge=1, le=3)
    turns_used: int = Field(strict=True, ge=1, le=3)
    expires_at: str = Field(min_length=20, max_length=40)

    @field_validator("expires_at")
    @classmethod
    def utc_expiry(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        offset = parsed.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("Conversation expiry must be a UTC timestamp")
        return value

    @model_validator(mode="after")
    def matching_revision(self):
        if self.revision != self.turns_used:
            raise ValueError("Invalid conversation revision")
        return self


class ClosedConversationReceipt(_ContextModel):
    state: Literal["closed"]
    reason: Literal["turn_limit", "byte_limit", "expired", "cleared", "scope_unavailable"]
    turns_used: int = Field(strict=True, ge=0, le=4)


ConversationReceipt = Annotated[
    ActiveConversationReceipt | ClosedConversationReceipt, Field(discriminator="state")
]
