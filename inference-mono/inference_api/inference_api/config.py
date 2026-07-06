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

    token_reset_mode: Literal["account_creation", "calendar_month", "weekly"] = "account_creation"
    token_reset_day: int = Field(default=1, ge=1, le=28)
    token_reset_weekday: int = Field(default=0, ge=0, le=6)  # 0 = Monday (weekly mode anchor)
    free_monthly_token_allowance: int = Field(default=0, ge=0)

    # --- Subnet-stake weekly quota (reads ht-indexer) --------------------- #
    # Reward continuous subnet delegators with a weekly, stake-proportional
    # inference-token quota. Off by default; when enabled the maintenance loop
    # reads the ht-indexer and writes a hypertensor/subnet_stake allowance
    # snapshot that the usage gate sums like any other crypto allowance.
    subnet_stake_quota_enabled: bool = False
    ht_indexer_graphql_url: str | None = None  # e.g. http://localhost:4350/graphql
    ht_indexer_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    subnet_stake_subnet_id: int | None = None  # which subnet's delegation counts
    subnet_stake_decimals: int = Field(default=18, ge=0, le=36)  # native-token decimals
    subnet_stake_min_days: int = Field(default=30, ge=0, le=3650)  # min continuous stake age
    subnet_stake_min_amount: int = Field(default=0, ge=0)  # min normalized stake to qualify
    subnet_stake_tokens_unit: int = Field(default=1, ge=1)  # normalized stake per unit
    subnet_stake_tokens_per_unit: int = Field(default=0, ge=0)  # tokens granted per unit
    subnet_stake_weekly_token_cap: int = Field(default=0, ge=0)  # 0 = uncapped
    subnet_stake_refresh_interval_seconds: int = Field(default=900, ge=30, le=86_400)

    mesh_router_url: str | None = None
    mesh_request_timeout_seconds: int = Field(default=120, ge=1, le=600)

    # --- SERVING / COORDINATION plane ------------------------------------- #
    # Provisioner (platform -> enclave credential sealing) ----------------- #
    provisioner_enabled: bool = False
    # Platform Ed25519 signing key used to sign /provision payloads. Either a
    # filesystem path to a 32-byte raw or 64-hex seed, or the seed hex inline.
    provisioner_signing_key_path: str | None = None
    provisioner_signing_key_hex: SecretStr | None = None
    # The platform provisioner's verify key hex (advertised to the miner's
    # validator_static_keys so the enclave accepts our provision payloads).
    provisioner_verify_key_hex: str | None = None
    # Dedicated Fernet key for at-rest encryption of provisioned tokens. This is
    # NOT secret_pepper: token confidentiality is independent of token hashing.
    provisioner_token_encryption_key: SecretStr | None = None
    attestation_ttl_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    token_ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    allow_dev_attestation: bool = False
    # After a successful provision, have the platform (which alone holds the freshly
    # minted enclave admin token) drive the TEE's POST /engine/start so the advertised
    # model is actually loaded and the proxy can serve real completions. Off by default
    # so the existing provisioner path/tests stay unchanged; the cross-process E2E sets
    # PROVISIONER_START_ENGINE=true. See ProvisionerService._maybe_start_engine.
    provisioner_start_engine: bool = False
    # Engine name the platform asks the TEE to launch when provisioner_start_engine is on.
    # In dev/mock mode the TEE ignores this and runs the bundled mock engine; the value
    # must still satisfy the TEE's StartRequest schema (Literal["vllm", "sglang"]).
    provisioner_engine_name: Literal["vllm", "sglang"] = "vllm"
    # Bound wall-clock wait for the TEE engine to report ready after /engine/start.
    provisioner_engine_start_timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    # Attestation verifier backend selection / credentials passthrough ----- #
    verifier_backend: Literal["auto", "stub", "nvidia_nras", "intel_pcs", "mock", "dev"] = "auto"
    nras_api_url: str | None = None
    nras_api_key: SecretStr | None = None
    pcs_api_url: str | None = None
    pcs_api_key: SecretStr | None = None

    # --- Attestation policy: NVIDIA NRAS (real backend) config ------------- #
    # Fed to talaris_attest.build_expected_claims via NvidiaPolicy. Unset (None)
    # fields keep the backend's own production-safe defaults. The production
    # NVIDIA path FAILS CLOSED unless jwks_url/expected_issuer/nvidia_root_pem
    # are supplied (so we never silently accept unverifiable GPU evidence).
    nras_jwks_url: str | None = None
    nras_expected_issuer: str | None = None
    nras_url: str | None = None
    # NVIDIA NRAS signing root: either an inline PEM or a filesystem path that is
    # read at policy-build time. Stored verbatim here; resolution lives in the
    # provisioner (so config stays declarative).
    nvidia_root_pem: str | None = None

    # --- Attestation policy: Intel TDX/PCS (real backend) config ----------- #
    # Production Intel defaults are already fully real (bundled Intel SGX Root CA
    # + live Intel PCS). require_collateral defaults True (production-safe);
    # allowed_tcb_statuses=None keeps the backend default set.
    intel_require_collateral: bool = True
    intel_allowed_tcb_statuses: Annotated[frozenset[str] | None, NoDecode] = None

    # --- Hardware-evidence requirements (passthrough to the factory) ------- #
    require_tdx_quote: bool = True
    require_gpu_evidence: bool = True

    # Chain mapping (Substrate read) --------------------------------------- #
    chain_rpc_url: str | None = None
    chain_required: bool = False
    chain_min_class: str | None = "Included"

    # TEE forwarding / timeouts -------------------------------------------- #
    tee_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    tee_attestation_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    tee_provision_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    tee_forward_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    forward_max_attempts: int = Field(default=3, ge=1, le=10)
    tls_pin_enforce: bool = True

    # Self-registration ---------------------------------------------------- #
    registration_key_type: Literal["sr25519", "ed25519"] = "sr25519"
    registration_nonce_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # Background maintenance intervals (seconds) --------------------------- #
    heartbeat_interval_seconds: int = Field(default=60, ge=5, le=3600)
    dereg_after_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    maintenance_interval_seconds: int = Field(default=120, ge=5, le=3600)
    health_stale_after_seconds: int = Field(default=300, ge=30, le=86_400)
    provisioner_loop_interval_seconds: int = Field(default=60, ge=5, le=3600)

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def split_lists(cls, value: Any) -> list[str]:
        return _split_csv(value)

    @field_validator("intel_allowed_tcb_statuses", mode="before")
    @classmethod
    def split_tcb_statuses(cls, value: Any) -> frozenset[str] | None:
        # Unset -> None (keep the backend's default TCB-status set). A non-empty
        # CSV (e.g. "UpToDate,SWHardeningNeeded") -> an explicit allowlist.
        if value is None:
            return None
        if isinstance(value, frozenset):
            return value
        items = _split_csv(value)
        return frozenset(items) if items else None

    @field_validator("mesh_router_url", "chain_rpc_url", mode="before")
    @classmethod
    def normalize_optional_url(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).rstrip("/")

    @model_validator(mode="after")
    def validate_subnet_stake_quota(self) -> "Settings":
        # Fail closed on misconfig: enabling the quota without a way to reach the
        # indexer (or without a target subnet) would silently grant nobody.
        if self.subnet_stake_quota_enabled:
            if not self.ht_indexer_graphql_url:
                raise ValueError("HT_INDEXER_GRAPHQL_URL is required when SUBNET_STAKE_QUOTA_ENABLED=true")
            if self.subnet_stake_subnet_id is None:
                raise ValueError("SUBNET_STAKE_SUBNET_ID is required when SUBNET_STAKE_QUOTA_ENABLED=true")
        return self

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
