from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from inference_api.config import settings


class APIKeyUsageSummary(BaseModel):
    api_key_id: uuid.UUID
    requests: int
    input_tokens: int
    output_tokens: int


class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo]


class InferenceRequest(BaseModel):
    model: str = "demo-inference-001"
    prompt: str = Field(min_length=1, max_length=20_000)
    max_tokens: int = Field(default=128, ge=1, le=4096)


class InferenceResponse(BaseModel):
    id: str
    object: Literal["inference"] = "inference"
    model: str
    output: str
    usage: dict[str, int]


class OpenAIChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["developer", "system", "user", "assistant", "tool", "function"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None


class OpenAIStreamOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    include_usage: bool = False


class OpenAIChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(default="demo-chat-001", min_length=1, max_length=120)
    messages: list[OpenAIChatMessage] = Field(min_length=1)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=4096)
    stream: bool = False
    stream_options: OpenAIStreamOptions | None = None
    temperature: float | None = None
    top_p: float | None = None
    n: int = Field(default=1, ge=1, le=1)
    stop: str | list[str] | None = None
    user: str | None = None

    @property
    def resolved_max_tokens(self) -> int:
        return self.max_completion_tokens or self.max_tokens or 128


class RouterReservationCreate(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=120)
    input_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)

    @model_validator(mode="after")
    def token_counts_within_limits(self) -> RouterReservationCreate:
        if self.input_tokens > settings.router_max_input_tokens:
            raise ValueError("input_tokens exceeds router maximum")
        if self.max_output_tokens > settings.router_max_output_tokens:
            raise ValueError("max_output_tokens exceeds router maximum")
        return self


class RouterUsageSettle(BaseModel):
    reservation_id: uuid.UUID
    request_id: str = Field(min_length=1, max_length=128)
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def token_counts_within_limits(self) -> RouterUsageSettle:
        if self.input_tokens > settings.router_max_input_tokens:
            raise ValueError("input_tokens exceeds router maximum")
        if self.output_tokens > settings.router_max_output_tokens:
            raise ValueError("output_tokens exceeds router maximum")
        return self


class RouterValidationResponse(BaseModel):
    allowed: bool
    reason: str
    user_id: uuid.UUID
    api_key_id: uuid.UUID
    model: str
    estimated_tokens: int
    estimated_charged_tokens: int
    remaining_tokens: int
    model_multiplier: Decimal


class RouterReservationResponse(BaseModel):
    reservation_id: uuid.UUID
    request_id: str
    status: str
    user_id: uuid.UUID
    api_key_id: uuid.UUID | None
    router_client_id: uuid.UUID | None
    model: str
    input_tokens: int
    output_tokens: int
    raw_total_tokens: int
    reserved_tokens: int
    charged_tokens: int
    remaining_tokens: int
    expires_at: datetime
    settled_at: datetime | None = None
    released_at: datetime | None = None
