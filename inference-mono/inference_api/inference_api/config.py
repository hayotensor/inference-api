from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TEE Inference API"
    app_env: Literal["local", "test", "production"] = "local"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://inference:inference@postgres:5432/inference"
    redis_url: str = "redis://redis:6379/0"
    rate_limit_enabled: bool = True
    rate_limit_fail_open: bool = True
    secret_pepper: SecretStr = Field(default=SecretStr("change-me-in-env"))

    cors_origins: Annotated[list[str], NoDecode] = []
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "testserver"]
    request_id_header: str = "X-Request-ID"

    router_reservation_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    router_max_input_tokens: int = Field(default=1_000_000, ge=1)
    router_max_output_tokens: int = Field(default=1_000_000, ge=1)

    token_reset_mode: Literal["account_creation", "calendar_month"] = "account_creation"
    token_reset_day: int = Field(default=1, ge=1, le=28)
    free_monthly_token_allowance: int = Field(default=0, ge=0)

    mesh_router_url: str | None = None
    mesh_request_timeout_seconds: int = Field(default=120, ge=1, le=600)

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def split_lists(cls, value: Any) -> list[str]:
        return _split_csv(value)

    @field_validator("mesh_router_url", mode="before")
    @classmethod
    def normalize_optional_url(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).rstrip("/")

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.app_env != "production":
            return self
        secret = self.secret_pepper.get_secret_value()
        if secret == "change-me-in-env" or len(secret) < 32:
            raise ValueError("SECRET_PEPPER must match the main API and be at least 32 random characters")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must be explicit in production")
        if self.rate_limit_enabled and self.rate_limit_fail_open:
            raise ValueError("RATE_LIMIT_FAIL_OPEN must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
