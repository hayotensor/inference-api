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
    # Public base URL of the user-facing frontend (dashboard + /chat). Used for
    # email links, the SIWE message domain/URI, and SSO.
    app_url: AnyHttpUrl | str = "http://localhost:3000"

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

    # SSO cookie token: a medium-TTL JWT the edge Worker verifies (shared secret)
    # to identify the logged-in user when routing to the /chat surface.
    sso_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    sso_audience: str = "talaris:sso"
    sso_ttl_seconds: int = Field(default=28_800, ge=300, le=604_800)
    verification_token_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    reset_password_token_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    oauth_state_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    session_secret: SecretStr = Field(default=SecretStr("change-me-in-env"))
    secret_pepper: SecretStr = Field(default=SecretStr("change-me-in-env"))

    cors_origins: Annotated[list[str], NoDecode] = []
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "testserver"]
    request_id_header: str = "X-Request-ID"

    # Reverse-proxy trust for client-IP determination (rate limiting, audit
    # logging). Entries are literal IPs or CIDR networks for hops that are
    # allowed to set X-Forwarded-For with a truthful value (e.g. the
    # cloudflared/tunnel container's docker-network address in production).
    # SAFE DEFAULT: empty, meaning X-Forwarded-For is never trusted and the
    # socket peer is used verbatim -- this is deliberately fail-closed so an
    # unconfigured deployment cannot be tricked by a client-supplied header.
    # See app/core/rate_limit.py:ClientIPMiddleware for how this interacts
    # with uvicorn's own --forwarded-allow-ips/FORWARDED_ALLOW_IPS handling.
    trusted_proxy_ips: Annotated[list[str], NoDecode] = []

    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    apple_client_id: str | None = None
    apple_client_secret: SecretStr | None = None

    # Mark new accounts email-verified at registration and skip sending the
    # verification email entirely. Intended for local development and demos,
    # where there is no real mailbox and the verification link would otherwise
    # have to be dug out of the console log. It also satisfies the
    # verified-contact gate on API-key creation (see app/api_keys/service.py),
    # so a fresh signup can mint a key immediately.
    #
    # SECURITY: leaving this on in production lets anyone register an address
    # they do not control and have it treated as proven, so it is OFF by
    # default and logged loudly at startup when enabled.
    auto_verify_new_users: bool = False

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
    # Sign-in-with-wallet (SIWE) login: synthetic-email domain for wallet-only
    # accounts + the EIP-4361 chain id presented in the sign-in message.
    wallet_user_email_domain: str = "wallet-users.inference.dev"
    wallet_siwe_chain_id: int = Field(default=1, ge=1)
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None

    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    api_key_default_rate_limit_per_minute: int = 120
    # HYP-45: rate used to express a key's pay-as-you-go wallet spend in USD for
    # its ``usd_limit_cents`` cap. Defaults to the smallest wallet top-up's
    # price ($10 / 1M credits, app/billing/plans.py) -- the highest per-credit
    # rate any bundle charges, so the cap errs on the side of stopping early.
    # MUST match inference_api's API_KEY_USD_CENTS_PER_MILLION_CREDITS, which
    # is what actually enforces the cap.
    api_key_usd_cents_per_million_credits: int = Field(default=1_000, ge=1)
    router_default_rate_limit_per_minute: int = Field(default=600, ge=1, le=60_000)
    router_reservation_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    router_max_input_tokens: int = Field(default=1_000_000, ge=1)
    router_max_output_tokens: int = Field(default=1_000_000, ge=1)

    stripe_secret_key: SecretStr | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_success_url: AnyHttpUrl | str = "http://localhost:3000/billing/success"
    stripe_cancel_url: AnyHttpUrl | str = "http://localhost:3000/billing/cancel"
    stripe_customer_portal_return_url: AnyHttpUrl | str = "http://localhost:3000/dashboard/billing"
    stripe_starter_price_id: str | None = None
    stripe_pro_price_id: str | None = None
    stripe_business_price_id: str | None = None
    stripe_portal_configuration_id: str | None = None
    stripe_starter_annual_price_id: str | None = None
    stripe_pro_annual_price_id: str | None = None
    stripe_business_annual_price_id: str | None = None
    # HYP-76. Left unset until scripts/stripe_sync_plans.py is run for this new
    # tier -- app/billing/plans.py's seeder tolerates a None price id here the
    # same way it does for every other plan (see looks_like_test_plan /
    # _plan_and_price), so an unseeded Unlimited plan is simply not purchasable
    # yet rather than crashing anything.
    stripe_unlimited_price_id: str | None = None
    stripe_unlimited_annual_price_id: str | None = None

    ethereum_rpc_url: str | None = None
    ethereum_erc20_contract_address: str | None = None
    ethereum_erc20_decimals: int = Field(default=18, ge=0, le=36)
    substrate_evm_rpc_url: str | None = None
    substrate_native_decimals: int = Field(default=18, ge=0, le=36)

    token_reset_mode: Literal["account_creation", "calendar_month", "weekly"] = "account_creation"
    token_reset_day: int = Field(default=1, ge=1, le=28)
    token_reset_weekday: int = Field(default=0, ge=0, le=6)  # 0 = Monday (weekly mode anchor)
    free_monthly_token_allowance: int = Field(default=0, ge=0)
    wallet_nonce_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    # Subnet-stake weekly quota (reporting parity; the indexer read + enforcement
    # live in the inference service). See inference_api.ht_indexer.
    subnet_stake_quota_enabled: bool = False

    @field_validator("cors_origins", "allowed_hosts", "trusted_proxy_ips", mode="before")
    @classmethod
    def split_lists(cls, value: Any) -> list[str]:
        return _split_csv(value)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.app_env != "production":
            return self
        if self.debug:
            raise ValueError(
                "DEBUG must be false in production (it enables Starlette's "
                "traceback response page for unhandled exceptions)"
            )
        weak = "change-me-in-env"
        for name in (
            "jwt_secret",
            "verification_token_secret",
            "reset_password_token_secret",
            "oauth_state_secret",
            "session_secret",
            "secret_pepper",
            "sso_secret",
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
        if self.wallet_user_email_domain == "wallet-users.inference.dev":
            raise ValueError("WALLET_USER_EMAIL_DOMAIN must be set to an owned domain in production")
        # HYP-56: a dev convenience that treats every registered address as
        # proven (and so satisfies the API-key verify gate) is refused outright.
        if self.auto_verify_new_users:
            raise ValueError("AUTO_VERIFY_NEW_USERS must not be enabled in production")
        return self

    @property
    def rate_limit_url(self) -> str:
        return self.rate_limit_storage_url or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
