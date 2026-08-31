"""Pydantic v2 mirror of the TEE container's attestation wire format.

Authoritative source (mirror field-for-field):
``tee_wrapper.security.attestation.AttestationDocument`` (a dataclass), plus the
three top-level keys the ``GET /attestation`` router injects into ``doc.to_dict()``
before returning it: ``boot_nonce``, ``fingerprint``, ``tls_pubkey_signature``.

This package does NOT recompute report_data (that lives in the verifier package).
It DOES expose ``report_data_inputs(doc)`` returning the ordered field map a
verifier consumes to recompute the digest independently.

Forward-compat: ``extra="allow"`` so additive producer fields survive a
round-trip (parse -> model_dump) without being silently dropped.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .version import (
    REPORT_DATA_DOMAIN_TAG,
    REPORT_DATA_FIELDS,
    WIRE_VERSION,
)


class PublicBundle(BaseModel):
    """The enclave's published public keys.

    Mirrors ``EnclaveIdentity.public_bundle()`` = {"verify_key", "sealing_key"}
    (both hex). ``verify_key`` is the Ed25519 verify key; ``sealing_key`` is the
    X25519 public key credentials are sealed to.
    """

    model_config = ConfigDict(extra="allow")

    verify_key: str
    sealing_key: str


class TdxMeasurements(BaseModel):
    """Best-effort parsed TDX quote measurements (loose).

    The producer's TDX collector emits a permissive measurement dict; we keep it
    open and only surface the report_data echo a verifier cares about.
    """

    model_config = ConfigDict(extra="allow")

    report_data: str | None = None


class TdxQuote(BaseModel):
    """A collected TDX quote (loose).

    The wire shape is producer/collector defined; we keep it open and only type
    the ``measurements`` sub-object cheaply.
    """

    model_config = ConfigDict(extra="allow")

    measurements: TdxMeasurements | None = None


class GpuEvidence(BaseModel):
    """NVIDIA GPU evidence payload (loose).

    The ``nvattest`` CLI JSON is producer-defined; kept open. Common fields are
    surfaced for convenience without constraining the wire format.
    """

    model_config = ConfigDict(extra="allow")

    result_code: int | None = None
    result_message: str | None = None
    evidences: list[Any] | None = None


class AttestationDocument(BaseModel):
    """Pydantic mirror of ``tee_wrapper.security.attestation.AttestationDocument``.

    Field order, names, types, defaults, and nullability mirror the producer
    dataclass exactly, plus the three router-injected top-level keys. ``evidence``
    is kept loose (a plain dict) because its shape depends on attestation mode
    (dev signature vs. NVIDIA GPU evidence + TDX quote); typed accessors are
    provided for the common sub-objects.

    ``extra="allow"`` keeps the model additively forward-compatible: a new
    producer field round-trips through ``model_dump()`` untouched.
    """

    model_config = ConfigDict(extra="allow")

    # --- Core dataclass fields (exact order from the producer) -------------
    mode: str
    nonce: str
    report_data: str
    public_bundle: PublicBundle
    miner: str | None
    evidence: dict[str, Any] = Field(default_factory=dict)
    tls_cert_fingerprint: str | None = None
    model_hash: str | None = None
    # Current usage hash-chain head; producer default is "" (not None).
    usage_chain_head: str = ""
    args_hash: str | None = None
    gpu_hash: str | None = None
    gpu_uuids: list[Any] = Field(default_factory=list)
    usage_count: int | None = None
    usage_total_tokens: int | None = None
    # In-band report_data framing so a verifier can recompute without the source.
    report_data_version: str = WIRE_VERSION
    report_data_domain_tag: str = REPORT_DATA_DOMAIN_TAG
    report_data_fields: list[str] = Field(
        default_factory=lambda: list(REPORT_DATA_FIELDS)
    )

    # --- Router-injected top-level keys (GET /attestation) -----------------
    # These are added by the attestation router AFTER doc.to_dict(); they are
    # part of the wire document even though they are not dataclass fields.
    boot_nonce: str | None = None
    fingerprint: str | None = None
    tls_pubkey_signature: str | None = None

    # --- Typed accessors (cheap convenience over the loose `evidence`) ------
    @property
    def gpu_evidence(self) -> GpuEvidence | None:
        """The NVIDIA GPU evidence sub-object, if present (nvidia mode)."""
        ev = self.evidence.get("gpu_evidence")
        return GpuEvidence.model_validate(ev) if isinstance(ev, dict) else None

    @property
    def tdx_quote(self) -> TdxQuote | None:
        """The TDX quote sub-object, if present and non-null (nvidia mode)."""
        q = self.evidence.get("tdx_quote")
        return TdxQuote.model_validate(q) if isinstance(q, dict) else None

    @property
    def dev_signature(self) -> str | None:
        """The enclave's Ed25519 dev signature over report_data (dev mode only)."""
        sig = self.evidence.get("dev_signature")
        return sig if isinstance(sig, str) else None


def report_data_inputs(doc: AttestationDocument) -> dict[str, Any]:
    """Return the ordered field map a verifier feeds into ``report_data()``.

    Keys are the producer's ``REPORT_DATA_FIELDS`` (after the domain tag); the
    map is insertion-ordered to match the length-prefixed input order. This does
    NOT recompute the digest — it only marshals the document's published inputs
    so the verifier package can recompute it independently.

    Notes mirroring the producer's encoding contract:
    - ``domain_tag`` is the published ``report_data_domain_tag``.
    - ``nonce`` is the hex string (raw bytes are ``bytes.fromhex(nonce)``).
    - ``identity_fpr`` is the enclave ``fingerprint``.
    - String fields are "" when the producer had them absent ("" is distinct
      from the absent encoding of the integer fields).
    - Integer fields (``usage_count``/``usage_total_tokens``) are ``None`` when
      absent (encoded as b"" by the producer, distinct from "0").
    """
    return {
        "domain_tag": doc.report_data_domain_tag,
        "nonce": doc.nonce,
        "identity_fpr": doc.fingerprint,
        "miner_hash": doc.miner or "",
        "tls_fpr": doc.tls_cert_fingerprint or "",
        "model_hash": doc.model_hash or "",
        "usage_chain_head": doc.usage_chain_head or "",
        "args_hash": doc.args_hash or "",
        "gpu_hash": doc.gpu_hash or "",
        "usage_count": doc.usage_count,
        "usage_total_tokens": doc.usage_total_tokens,
    }


__all__ = [
    "PublicBundle",
    "TdxMeasurements",
    "TdxQuote",
    "GpuEvidence",
    "AttestationDocument",
    "report_data_inputs",
]
