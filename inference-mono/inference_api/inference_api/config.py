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
    # IPs/CIDRs of reverse-proxy hops this plane trusts to set X-Forwarded-For.
    # Empty by default (fail closed to the raw socket peer) -- mirrors
    # app/core/rate_limit.py's ``trusted_proxy_ips`` on the control plane. Only
    # consumer today is inference_api.client_ip.resolve_client_ip (see HYP-67's
    # guest-inference IP ceiling).
    trusted_proxy_ips: Annotated[list[str], NoDecode] = []
    # When the immediate peer is one of ``trusted_proxy_ips``, prefer the
    # ``CF-Connecting-IP`` header (set by Cloudflare / cloudflared to the real
    # visitor IP, overwriting any client-supplied value) over walking
    # ``X-Forwarded-For``. Off by default: only enable it when this plane is
    # reachable solely through Cloudflare (e.g. bound to loopback behind a
    # cloudflared tunnel), otherwise a direct client could set the header. It
    # is never honored from an untrusted peer either way.
    trust_cf_connecting_ip: bool = False

    # HYP-45: conversion used to enforce an API key's ``usd_limit_cents`` cap on
    # its pay-as-you-go (wallet-funded) spend. Defaults to the smallest wallet
    # top-up's price ($10 / 1M credits, api/app/billing/plans.py), the highest
    # per-credit rate any bundle charges, so the cap errs on stopping early.
    # Must match the control plane's API_KEY_USD_CENTS_PER_MILLION_CREDITS.
    api_key_usd_cents_per_million_credits: int = Field(default=1_000, ge=1)

    # --- Anonymous "sandbox" inference (HYP-67) ---------------------------- #
    # The homepage's unauthenticated try-it-now box. Off switch kept separate
    # from ``rate_limit_enabled`` -- this endpoint's two daily ceilings
    # (guest_limits.check_and_increment_guest_limits) are ALWAYS enforced
    # whenever the endpoint is enabled, and deliberately FAIL CLOSED on a Redis
    # error (unlike the rest of this plane's rate limiting, which honors
    # ``rate_limit_fail_open``) -- this is the platform's first anonymous-facing
    # compute surface, so a Redis outage must not silently grant free inference.
    guest_inference_enabled: bool = True
    # Explicit operator override for the one model anonymous requests are
    # always forced onto. Unset (default) -> resolved at request time to
    # whichever ACTIVE model_pricing row has the lowest token_multiplier (see
    # inference_api.pricing.resolve_guest_model_id).
    guest_model_id: str | None = None
    guest_max_tokens: int = Field(default=256, ge=1, le=4096)
    guest_client_daily_limit: int = Field(default=10, ge=0)
    guest_ip_daily_limit: int = Field(default=30, ge=0)

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
    # Dev-only echo fallback (SAFE BY DEFAULT: OFF) ------------------------- #
    # When no attested+healthy miner can serve a model, execute_inference() can
    # fall back to an in-process "Echo: ..." response for local development
    # convenience. This bypasses the TEE entirely and MUST NOT run unless an
    # operator explicitly opts in here. It used to run automatically whenever
    # app_env != "production", which let an unhosted model (or a momentarily
    # unselectable miner for a perfectly real model) silently return a
    # fabricated, billed HTTP 200 in every non-production environment. Off by
    # default so a missing/forgotten env var fails closed into a proper
    # 404 (model_not_found) / 503 (no_capacity) instead of fabricating output.
    allow_dev_echo_fallback: bool = False

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
    # Extra engine launch flags the platform passes to /engine/start (rendered by the TEE
    # as --key value; underscores become dashes). Used e.g. to pin a Blackwell-correct
    # attention backend for SGLang ({"attention_backend": "triton"}). JSON in the env var.
    provisioner_engine_args: dict = Field(default_factory=dict)

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
    # Enclave SOFTWARE pin (HYP-56). Without it, any software running in a genuine
    # TD on genuine hardware attests successfully: the DCAP chain proves "real Intel
    # TDX", not "the published Talaris enclave build". INTEL_TDX_MEASUREMENTS is the
    # published-build allowlist: a JSON list of {mrtd, rtmr0..rtmr3, label} objects,
    # inline or "@/path/to/builds.json" (see talaris_attest.load_measurement_allowlist).
    # When set, a miner attests only if its hardware-verified MRTD/RTMRs match an
    # entry. INTEL_REQUIRE_MEASUREMENT_PIN=true makes an UNSET allowlist fail every
    # attestation (the production opt-in once a build's measurements are published).
    intel_tdx_measurements: str | None = None
    intel_require_measurement_pin: bool = False

    # --- Hardware-evidence requirements (passthrough to the factory) ------- #
    require_tdx_quote: bool = True
    require_gpu_evidence: bool = True

    # Chain mapping (Substrate read) --------------------------------------- #
    chain_rpc_url: str | None = None
    chain_required: bool = False
    chain_min_class: str | None = "Included"
    # The Hypertensor subnet this platform serves (the real on-chain subnet id, not a
    # friendly id). The class gate looks the miner up as
    # Network.PeerIdSubnetNodeId(subnet_id, peer_id) -> Network.SubnetNodesData, so
    # it is required for the chain lookup.
    chain_subnet_id: int | None = None
    # Subnet epoch derivation: epoch = block_number // epoch_length, matching the
    # subnet's own ``Hypertensor.get_epoch``. The length is read from the chain
    # constant ``Network::EpochLength`` and cached; this setting is the fallback
    # used when no RPC is configured (dev/tests) and to sanity-check the read.
    subnet_epoch_length_blocks: int = Field(default=20, ge=1)
    subnet_block_seconds: int = Field(default=6, ge=1)
    # How long a resolved current-epoch value may be reused before re-reading the
    # chain. Kept well under one epoch so a stamped row lands in the right bucket.
    subnet_epoch_cache_seconds: float = Field(default=10.0, gt=0, le=600)

    # Miner statistics / signed epoch snapshots ----------------------------- #
    miner_stats_enabled: bool = False
    # Epochs to wait past the one being finalized before snapshotting it. The
    # reservation-settlement and receipts-pull paths are both asynchronous, so
    # finalizing the moment an epoch ends would publish an undercount — and a
    # published epoch can never be corrected (it is immutable by design).
    miner_stats_settle_lag_epochs: int = Field(default=1, ge=0, le=64)
    # Cap on how many un-finalized past epochs one maintenance pass will close, so
    # a long outage catches up gradually instead of stalling the whole pass.
    miner_stats_max_finalize_per_pass: int = Field(default=8, ge=1, le=256)
    tee_receipts_pull_enabled: bool = False
    tee_receipts_page_limit: int = Field(default=500, ge=1, le=10_000)
    tee_receipts_timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    # TEE forwarding / timeouts -------------------------------------------- #
    tee_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    tee_attestation_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    tee_provision_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    tee_forward_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    forward_max_attempts: int = Field(default=3, ge=1, le=10)
    tls_pin_enforce: bool = True

    # Self-registration ---------------------------------------------------- #
    # ed25519 is the only scheme the shipped subnet node signs with (its libp2p key),
    # and the only one whose key maps to the libp2p peer ID the chain records.
    registration_key_type: Literal["sr25519", "ed25519"] = "ed25519"
    registration_nonce_ttl_seconds: int = Field(default=300, ge=30, le=3600)

    # Background maintenance intervals (seconds) --------------------------- #
    heartbeat_interval_seconds: int = Field(default=60, ge=5, le=3600)
    dereg_after_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    maintenance_interval_seconds: int = Field(default=120, ge=5, le=3600)
    health_stale_after_seconds: int = Field(default=300, ge=30, le=86_400)
    provisioner_loop_interval_seconds: int = Field(default=60, ge=5, le=3600)

    @field_validator("cors_origins", "allowed_hosts", "trusted_proxy_ips", mode="before")
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

    @field_validator("intel_tdx_measurements", mode="after")
    @classmethod
    def validate_tdx_measurements(cls, value: str | None) -> str | None:
        # Parse once at startup so a typo fails loudly instead of disabling the pin.
        if value is None or not str(value).strip():
            return None
        from talaris_attest import load_measurement_allowlist

        load_measurement_allowlist(value)
        return value

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
        if self.allow_dev_echo_fallback:
            raise ValueError("ALLOW_DEV_ECHO_FALLBACK must not be enabled in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
