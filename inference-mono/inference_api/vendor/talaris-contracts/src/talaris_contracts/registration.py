"""Self-registration / miner-state advertisement wire format.

These models mirror the inference-subnet miner-state gossip shapes where
practical (``inference.pubsub.miner_state.publisher.MinerStateData`` /
``MinerHostedModelState`` / ``MinerTeeState``), specialized for a TEE-backed
miner announcing itself (its hotkey, peer id, hosted models, TEEs, and health)
with a signature over a canonical nonce/timestamp-bound message.

These are kept additively forward-compatible (``extra="allow"``); field names
follow the inference-subnet models so a payload can be cross-consumed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field


class HostedModel(BaseModel):
    """A model the miner advertises as hosted.

    Field names mirror ``MinerHostedModelState`` where practical so this can be
    cross-consumed by the inference-subnet receiver. ``model_id`` is required;
    most descriptive fields are optional.
    """

    model_config = ConfigDict(extra="allow")

    model_id: str
    model_version: str | None = None
    # Per-model-enclave direct endpoint (the SNI host behind the miner's payload-blind
    # proxy) the API forwards inference to and attests. Advertisement only — NOT part of
    # the signed registration message; the per-model attestation provides the real bind.
    tee_endpoint: str | None = None
    status: str = "loaded"
    loaded: bool = True
    model_name: str | None = None
    model_family: str | None = None
    architecture: str | None = None
    parameter_count_b: float | None = None
    model_format: str | None = None
    quantization: str | None = None
    dtype: str | None = None
    context_length: int | None = None
    max_sequence_length: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    server_ids: list[str] = Field(default_factory=list)
    tee_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeeDescriptor(BaseModel):
    """A TEE the miner advertises.

    Field names mirror ``MinerTeeState`` where practical. ``tee_id`` is required.
    """

    model_config = ConfigDict(extra="allow")

    tee_id: str
    status: str = "ready"
    kind: str | None = None
    available_slots: int | None = None


class MinerHealth(BaseModel):
    """Liveness / capacity summary advertised by the miner.

    Kept loose: the producing miner may attach arbitrary health signals. ``state``
    mirrors the inference-subnet ``ServerState`` string values ("offline",
    "joining", "online").
    """

    model_config = ConfigDict(extra="allow")

    state: str = "online"
    healthy: bool = True
    available_tee_count: int | None = None
    tee_count: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class SelfRegistration(BaseModel):
    """A miner's signed self-registration announcement.

    Carries the miner identity (``hotkey``, ``peer_id``, ``subnet_node_id``), its
    TEE endpoint, advertised inventory (``hosted_models``, ``tees``), a ``health``
    summary, and a ``signature`` over a canonical message (typically binding
    ``nonce`` / ``timestamp`` for replay protection — the exact canonicalization
    is defined by the registration verifier).
    """

    model_config = ConfigDict(extra="allow")

    hotkey: str
    peer_id: str
    subnet_node_id: int
    tee_endpoint: str
    hosted_models: list[HostedModel] = Field(default_factory=list)
    tees: list[TeeDescriptor] = Field(default_factory=list)
    health: MinerHealth = Field(default_factory=MinerHealth)
    signature: str
    nonce: str | None = None
    timestamp: int | None = None


# --------------------------------------------------------------------------- #
# Self-registration signature: the SINGLE source of truth for the bytes a miner
# signs and the inference-api endpoint verifies. The inference-subnet miner
# client (signer) and the inference-api registration endpoint (verifier) MUST
# both go through these helpers so they cannot diverge on canonicalization.
# --------------------------------------------------------------------------- #

# Domain-separation tag so a self-registration signature can never collide with
# or be reinterpreted as any other message a miner hotkey signs (e.g. the TEE
# /bind message b"tee-wrapper/bind/v1", or on-chain extrinsics).
REGISTRATION_DOMAIN_TAG = b"talaris/self-registration/v1"


def registration_signing_message(
    *,
    hotkey: str,
    peer_id: str,
    subnet_node_id: int,
    tee_endpoint: str,
    nonce: str | None,
    timestamp: int | None,
    hosted_model_ids: Iterable[str],
) -> bytes:
    """Deterministic, domain-separated bytes the miner hotkey signs.

    Binds the miner identity (``hotkey``, ``peer_id``, ``subnet_node_id``), the
    advertised ``tee_endpoint``, an order-independent digest-free **sorted** list
    of advertised model ids, and a ``nonce``/``timestamp`` for replay protection.
    The body is canonical JSON (sorted keys, compact separators) so signer and
    verifier always produce identical bytes regardless of dict/field ordering.
    """
    body = {
        "hotkey": hotkey.lower(),
        "models": sorted(str(m) for m in hosted_model_ids),
        "nonce": nonce or "",
        "peer_id": peer_id,
        "subnet_node_id": int(subnet_node_id),
        "tee_endpoint": tee_endpoint,
        "timestamp": int(timestamp) if timestamp is not None else 0,
    }
    return REGISTRATION_DOMAIN_TAG + json.dumps(
        body, separators=(",", ":"), sort_keys=True
    ).encode()


def registration_message_for(reg: "SelfRegistration") -> bytes:
    """Build the canonical signing message for a ``SelfRegistration`` instance."""
    return registration_signing_message(
        hotkey=reg.hotkey,
        peer_id=reg.peer_id,
        subnet_node_id=reg.subnet_node_id,
        tee_endpoint=reg.tee_endpoint,
        nonce=reg.nonce,
        timestamp=reg.timestamp,
        hosted_model_ids=[m.model_id for m in reg.hosted_models],
    )


def _verify_signature(
    pubkey_hex: str, message: bytes, signature_hex: str, key_type: str
) -> bool:
    """Mirror of ``tee_wrapper.security.miner.verify_miner_signature``.

    ed25519 (pynacl, always available) or sr25519 (optional py-sr25519-bindings).
    Hypertensor/Bittensor hotkeys are sr25519 by default.
    """
    try:
        raw_pk = bytes.fromhex(pubkey_hex)
        raw_sig = bytes.fromhex(signature_hex)
    except ValueError:
        return False

    if key_type == "ed25519":
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        try:
            VerifyKey(raw_pk).verify(message, raw_sig)
            return True
        except (BadSignatureError, ValueError):
            return False

    if key_type == "sr25519":
        try:
            import sr25519  # type: ignore  # py-sr25519-bindings (optional extra)
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                "sr25519 hotkeys require py-sr25519-bindings; "
                "install talaris-contracts[sr25519]"
            ) from exc
        try:
            return bool(sr25519.verify(raw_sig, message, raw_pk))
        except Exception:  # noqa: BLE001 - any binding error means invalid
            return False

    raise ValueError(f"unknown key_type: {key_type}")


def verify_registration_signature(
    reg: "SelfRegistration", *, key_type: str = "sr25519"
) -> bool:
    """Verify ``reg.signature`` over the canonical message under ``reg.hotkey``.

    The ``hotkey`` is the miner's PUBLIC key hex (same key bound via the TEE
    ``/bind`` flow, so ``sha256(bytes.fromhex(hotkey))`` equals the attestation
    ``miner`` field — that is how inference-api ties registration to attestation).
    """
    message = registration_message_for(reg)
    return _verify_signature(reg.hotkey, message, reg.signature, key_type)


def sign_registration_ed25519(
    *,
    signing_key: "Any",  # nacl.signing.SigningKey
    peer_id: str,
    subnet_node_id: int,
    tee_endpoint: str,
    hosted_models: list["HostedModel"] | None = None,
    tees: list["TeeDescriptor"] | None = None,
    health: "MinerHealth | None" = None,
    nonce: str | None = None,
    timestamp: int | None = None,
) -> "SelfRegistration":
    """Build a signed ``SelfRegistration`` using an ed25519 hotkey (the testable
    path; the miner's hotkey verify key hex becomes ``hotkey``).

    sr25519 hotkeys sign with their own keypair on the subnet side and set
    ``key_type='sr25519'`` for verification — they MUST sign the bytes returned by
    ``registration_signing_message`` so this stays the single source of truth.
    """
    hotkey_hex = bytes(signing_key.verify_key).hex()
    models = hosted_models or []
    message = registration_signing_message(
        hotkey=hotkey_hex,
        peer_id=peer_id,
        subnet_node_id=subnet_node_id,
        tee_endpoint=tee_endpoint,
        nonce=nonce,
        timestamp=timestamp,
        hosted_model_ids=[m.model_id for m in models],
    )
    signature = signing_key.sign(message).signature.hex()
    return SelfRegistration(
        hotkey=hotkey_hex,
        peer_id=peer_id,
        subnet_node_id=subnet_node_id,
        tee_endpoint=tee_endpoint,
        hosted_models=models,
        tees=tees or [],
        health=health or MinerHealth(),
        signature=signature,
        nonce=nonce,
        timestamp=timestamp,
    )


def registration_miner_hash(reg: "SelfRegistration") -> str:
    """sha256 of the miner pubkey (``hotkey``) — equals the attestation ``miner``
    field, binding a registration to its enclave attestation."""
    return hashlib.sha256(bytes.fromhex(reg.hotkey)).hexdigest()


__all__ = [
    "HostedModel",
    "TeeDescriptor",
    "MinerHealth",
    "SelfRegistration",
    "REGISTRATION_DOMAIN_TAG",
    "registration_signing_message",
    "registration_message_for",
    "verify_registration_signature",
    "sign_registration_ed25519",
    "registration_miner_hash",
]
