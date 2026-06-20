from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.api_keys.schemas import validate_future_expiration
from app.db.models.service_client import ServiceClientRole


class ServiceClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: ServiceClientRole = ServiceClientRole.router
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)

    @field_validator("expires_at")
    @classmethod
    def expires_at_is_future(cls, value: datetime | None) -> datetime | None:
        return validate_future_expiration(value)


class ServiceClientRead(BaseModel):
    id: uuid.UUID
    name: str
    role: ServiceClientRole
    prefix: str
    last_four: str
    rate_limit_per_minute: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class ServiceClientCreateResponse(ServiceClientRead):
    token: str


class ServiceClientsResponse(BaseModel):
    data: list[ServiceClientRead]
    total: int
    limit: int
    offset: int
