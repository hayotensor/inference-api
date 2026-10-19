import ipaddress
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.security import is_past
from app.db.models.api_key import APIKeyEnvironment, APIKeyLimitPeriod

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


MAX_ALLOWED_IPS = 50


def validate_allowed_ips(value: list[str] | None) -> list[str] | None:
    """Normalize an optional client IP allowlist (HYP-45).

    Same opt-in shape as ``allowed_models``: ``None``/empty = usable from any
    IP. Each entry is a literal IPv4/IPv6 address or a CIDR network; entries
    are canonicalized (``10.0.0.7/8`` -> ``10.0.0.0/8``, IPv6 compressed) and
    de-duplicated so the serving plane only ever sees well-formed values.
    """
    if not value:
        return None
    if len(value) > MAX_ALLOWED_IPS:
        raise ValueError(f"allowed_ips may contain at most {MAX_ALLOWED_IPS} entries")
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        entry = raw.strip()
        if not entry:
            raise ValueError("allowed_ips entries must be non-empty strings")
        try:
            if "/" in entry:
                normalized = str(ipaddress.ip_network(entry, strict=False))
            else:
                normalized = str(ipaddress.ip_address(entry))
        except ValueError:
            raise ValueError(f"{entry!r} is not a valid IP address or CIDR range") from None
        if normalized not in seen:
            seen.add(normalized)
            cleaned.append(normalized)
    return cleaned or None


def validate_spend_limits(
    limit_period: APIKeyLimitPeriod | str | None,
    credit_limit: int | None,
    usd_limit_cents: int | None,
) -> None:
    """A credit or USD cap is meaningless without the window it applies to."""
    if (credit_limit is not None or usd_limit_cents is not None) and limit_period is None:
        raise ValueError("limit_period is required when credit_limit or usd_limit_cents is set")


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    environment: APIKeyEnvironment = APIKeyEnvironment.test
    scopes: list[str] = Field(default_factory=lambda: DEFAULT_SCOPES.copy())
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)
    # Optional: restrict this key to a specific set of model ids. Unset/empty
    # means unrestricted -- the key can reach every model, same as today.
    allowed_models: list[str] | None = None
    # Optional: restrict this key to client IPs/CIDRs. Unset/empty = any IP.
    allowed_ips: list[str] | None = None
    # Optional spend caps (HYP-45); both share ``limit_period``.
    limit_period: APIKeyLimitPeriod | None = None
    credit_limit: int | None = Field(default=None, ge=1, le=10**15)
    usd_limit_cents: int | None = Field(default=None, ge=1, le=10**12)

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

    @field_validator("allowed_ips")
    @classmethod
    def allowed_ips_are_valid(cls, value: list[str] | None) -> list[str] | None:
        return validate_allowed_ips(value)

    @model_validator(mode="after")
    def limits_have_period(self) -> "APIKeyCreate":
        validate_spend_limits(self.limit_period, self.credit_limit, self.usd_limit_cents)
        return self


class APIKeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scopes: list[str] | None = None
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=60_000)
    allowed_models: list[str] | None = None
    allowed_ips: list[str] | None = None
    limit_period: APIKeyLimitPeriod | None = None
    credit_limit: int | None = Field(default=None, ge=1, le=10**15)
    usd_limit_cents: int | None = Field(default=None, ge=1, le=10**12)

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

    @field_validator("allowed_ips")
    @classmethod
    def allowed_ips_are_valid(cls, value: list[str] | None) -> list[str] | None:
        return validate_allowed_ips(value)


class APIKeyRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    environment: APIKeyEnvironment
    prefix: str
    last_four: str
    scopes: list[str]
    allowed_models: list[str] | None
    allowed_ips: list[str] | None = None
    limit_period: APIKeyLimitPeriod | None = None
    credit_limit: int | None = None
    usd_limit_cents: int | None = None
    rate_limit_per_minute: int
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class APIKeyCreateResponse(APIKeyRead):
    key: str


class APIKeySpendRead(BaseModel):
    """Current-period spend for a key's caps (HYP-45). Mirrors the numbers the
    serving plane enforces against (inference_api/key_limits.py)."""

    api_key_id: uuid.UUID
    limit_period: APIKeyLimitPeriod | None
    period_start: datetime | None
    resets_at: datetime | None
    credits_used: int
    usd_cents_used: int
    credit_limit: int | None
    usd_limit_cents: int | None


class APIKeyUsageSummary(BaseModel):
    api_key_id: uuid.UUID
    requests: int
    input_tokens: int
    output_tokens: int
