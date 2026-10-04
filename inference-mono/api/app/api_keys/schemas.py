import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.security import is_past
from app.db.models.api_key import APIKeyEnvironment

ALLOWED_API_KEY_SCOPES = frozenset({"models:read", "inference:write", "usage:read"})
DEFAULT_SCOPES = sorted(ALLOWED_API_KEY_SCOPES)


def validate_api_key_scopes(scopes: list[str]) -> list[str]:
    unknown = sorted(set(scopes) - ALLOWED_API_KEY_SCOPES)
    if unknown:
        raise ValueError(f"Unknown API key scopes: {', '.join(unknown)}")
    if not scopes:
        raise ValueError("At least one API key scope is required")
    return scopes


def validate_future_expiration(value: datetime | None) -> datetime | None:
    if value is not None and is_past(value):
        raise ValueError("expires_at must be in the future")
    return value


def validate_allowed_models(value: list[str] | None) -> list[str] | None:
    """Normalize an optional model allowlist.

    ``None`` (or an empty list) means unrestricted -- the key can reach every
    model, same as an unset ``scopes`` default would if that were allowed.
    Entries are stripped and de-duplicated (order preserved) so trivially
    different submissions ("gpt-4", "gpt-4 ") don't create surprising
    near-duplicate restrictions.
    """
    if not value:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for model_id in value:
        model_id = model_id.strip()
        if not model_id:
            raise ValueError("allowed_models entries must be non-empty strings")
        if model_id not in seen:
            seen.add(model_id)
            cleaned.append(model_id)
    return cleaned or None


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    environment: APIKeyEnvironment = APIKeyEnvironment.test
    scopes: list[str] = Field(default_factory=lambda: DEFAULT_SCOPES.copy())
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)
    # Optional: restrict this key to a specific set of model ids. Unset/empty
    # means unrestricted -- the key can reach every model, same as today.
    allowed_models: list[str] | None = None

    @field_validator("scopes")
    @classmethod
    def scopes_are_known(cls, value: list[str]) -> list[str]:
        return validate_api_key_scopes(value)

    @field_validator("expires_at")
    @classmethod
    def expires_at_is_future(cls, value: datetime | None) -> datetime | None:
        return validate_future_expiration(value)

    @field_validator("allowed_models")
    @classmethod
    def allowed_models_are_clean(cls, value: list[str] | None) -> list[str] | None:
        return validate_allowed_models(value)


class APIKeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)
    allowed_models: list[str] | None = None

    @field_validator("scopes")
    @classmethod
    def scopes_are_known(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return validate_api_key_scopes(value)

    @field_validator("expires_at")
    @classmethod
    def expires_at_is_future(cls, value: datetime | None) -> datetime | None:
        return validate_future_expiration(value)

    @field_validator("allowed_models")
    @classmethod
    def allowed_models_are_clean(cls, value: list[str] | None) -> list[str] | None:
        return validate_allowed_models(value)


class APIKeyRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    environment: APIKeyEnvironment
    prefix: str
    last_four: str
    scopes: list[str]
    allowed_models: list[str] | None
    rate_limit_per_minute: int
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class APIKeyCreateResponse(APIKeyRead):
    key: str


class APIKeyUsageSummary(BaseModel):
    api_key_id: uuid.UUID
    requests: int
    input_tokens: int
    output_tokens: int
