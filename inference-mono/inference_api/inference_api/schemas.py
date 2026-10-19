from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from inference_api.config import settings

# Hard cap on ``InferenceRequest.prompt``. Shared with product_routes so the
# chat-completions path (which builds an InferenceRequest by hand from OpenAI
# messages, rather than having FastAPI bind it as the request body) can name
# the exact same limit in its own 4xx instead of hardcoding a second copy of
# this number that could silently drift from the one enforced here.
PROMPT_MAX_LENGTH = 20_000


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
    # Non-standard additive field (OpenAI clients ignore unknown keys, so this
    # stays schema-compatible): "available" means an attested+healthy miner can
    # serve it right now; "degraded" means it is a real, registered model whose
    # miner(s) are currently unattested/unhealthy/stale — surfaced instead of
    # silently dropping the model from the catalog. See product_routes.list_models.
    status: Literal["available", "degraded"] = "available"


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo]


class InferenceRequest(BaseModel):
    model: str = "demo-inference-001"
    prompt: str = Field(min_length=1, max_length=PROMPT_MAX_LENGTH)
    max_tokens: int = Field(default=128, ge=1, le=4096)


class AttestationReceipt(BaseModel):
    """A platform-signed, independently-verifiable statement binding ONE
    inference response to the serving enclave's most recently VERIFIED TEE
    attestation (see ``inference_api.provisioner.service``, which is the only
    place ``model_hash``/``enclave_verify_key``/``tls_cert_fingerprint`` are
    ever set, and only after ``talaris_attest.verify_attestation`` passed).

    Signed with the SAME platform Ed25519 key that signs ``GET /allowlist`` and
    ``GET /subnet/stats/*``. Verify with ``inference_api.attestation.
    verify_inference_receipt(receipt, platform_pubkey)`` against the PINNED key
    published at ``GET /attest`` -- never trust ``signer_pubkey`` on the
    receipt itself, it is carried for convenience/diagnostics only.

    This is not a raw hardware quote re-delivered per request: the hardware
    quote is checked once, at provisioning time. What this proves is that
    THIS SPECIFIC response was, in the platform's own cryptographically signed
    voice, attributed to an enclave identity that passed that check.
    """

    kind: Literal["inference-receipt/v1"] = "inference-receipt/v1"
    request_id: str
    model_id: str
    model_hash: str | None = None
    enclave_verify_key: str | None = None
    tls_cert_fingerprint: str | None = None
    miner_hash: str | None = None
    attestation_mode: str | None = None
    attestation_verified_at: int | None = None
    signature: str
    signer_pubkey: str


class InferenceResponse(BaseModel):
    id: str
    object: Literal["inference"] = "inference"
    model: str
    output: str
    usage: dict[str, int]
    # REQUIRED (no default): every code path that builds this response must say
    # explicitly whether a real attested miner served it. True only for a real,
    # attested-miner-served completion -- and, when true, ``attestation`` below
    # is ALWAYS populated with a receipt a third party can independently verify
    # (see AttestationReceipt). False only when the opt-in dev echo fallback
    # answered instead (execute_inference / _execute_inference_mesh_fallback)
    # -- an unmistakable, machine-readable signal that this output is
    # fabricated and the TEE was bypassed.
    attested: bool
    attestation: AttestationReceipt | None = None


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
