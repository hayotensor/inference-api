from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Inference API"
    app_env: Literal["local", "test", "production"] = "local"
    debug: bool = False
    api_base_url: AnyHttpUrl | str = "http://localhost:8000"

    database_url: str = "postgresql+asyncpg://inference:inference@postgres:5432/inference"
    redis_url: str = "redis://redis:6379/0"
    rate_limit_storage_url: str | None = None
    rate_limit_fail_open: bool = True
    rate_limit_enabled: bool = True
    token_revocation_fail_open: bool = True

    jwt_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    jwt_audience: str = "fastapi-users:auth"
    jwt_lifetime_seconds: int = 900
    refresh_token_lifetime_days: int = 30
    verification_token_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    reset_password_token_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    oauth_state_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    session_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    secret_pepper: SecretStr = Field(default=SecretStr("change-me-in-env"))

    cors_origins: Annotated[list[str], NoDecode] = []
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "testserver"]
    request_id_header: str = "X-Request-ID"

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    apple_client_id: str | None = None
    apple_client_secret: SecretStr | None = None

    email_provider: Literal["console", "smtp", "sendgrid", "resend", "ses"] = "console"
    email_from: str = "noreply@example.com"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    sendgrid_api_key: SecretStr | None = None
    resend_api_key: SecretStr | None = None
    aws_region: str = "us-east-1"

    sms_provider: Literal["console", "twilio", "aws_sns"] = "console"
    default_phone_region: str = "US"
    phone_user_email_domain: str = "phone-users.inference.dev"
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None

    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    api_key_default_rate_limit_per_minute: int = 120

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def split_lists(cls, value: Any) -> list[str]:
        return _split_csv(value)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env != "production":
            return self
        weak = "change-me-in-env"
        for name in (
            "jwt_secret",
            "verification_token_secret",
            "reset_password_token_secret",
            "oauth_state_secret",
            "session_secret",
            "secret_pepper",
        ):
            secret_value = getattr(self, name).get_secret_value()
            if secret_value == weak or len(secret_value) < 32:
                raise ValueError(f"{name.upper()} must be set to at least 32 random characters")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must be explicit in production")
        if not self.cors_origins:
            raise ValueError("CORS_ORIGINS must be explicit in production")
        if self.rate_limit_fail_open:
            raise ValueError("RATE_LIMIT_FAIL_OPEN must be false in production")
        if self.token_revocation_fail_open:
            raise ValueError("TOKEN_REVOCATION_FAIL_OPEN must be false in production")
        if self.phone_user_email_domain == "phone-users.inference.dev":
            raise ValueError("PHONE_USER_EMAIL_DOMAIN must be set to an owned domain in production")
        return self

    @property
    def rate_limit_url(self) -> str:
        return self.rate_limit_storage_url or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
