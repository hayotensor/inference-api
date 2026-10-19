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
import ipaddress
import json
import os
import socket
from typing import Any, Iterable
from urllib.parse import urlparse

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
    reg: "SelfRegistration", *, key_type: str = "ed25519"
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


# --------------------------------------------------------------------------- #
# tee_endpoint SSRF guard.
#
# ``tee_endpoint`` (both ``SelfRegistration.tee_endpoint`` and the per-model
# ``HostedModel.tee_endpoint``) is a URL the PLATFORM later dials verbatim
# (inference-api's provisioner and forwarder). An unvalidated miner-supplied
# URL is a classic SSRF primitive: a miner can point it at the cloud instance
# metadata service (169.254.169.254), an internal admin panel, or any other
# service reachable from the platform's network.
#
# This validator is intentionally NOT wired in as a pydantic field validator
# on the models above: doing DNS resolution (see below) on every construction
# of a ``SelfRegistration``/``HostedModel`` would run inside the MINER's own
# process too (this module is shared with the signer), where "is this endpoint
# safe for the platform to dial" is meaningless and where test/dev code
# routinely builds these objects with placeholder hostnames. Instead,
# inference-api calls ``validate_tee_endpoint`` explicitly at the two places
# that matter: the ``/miners/register`` route (before the endpoint is ever
# persisted) and the provisioner (right before it is dialed).
# --------------------------------------------------------------------------- #


class TeeEndpointError(ValueError):
    """``tee_endpoint`` failed the SSRF-safety check (bad scheme, or resolves
    into a disallowed address range)."""


# One escape hatch, read directly from the process environment (NOT from any
# ``.env`` file) so an operator can allow private/internal endpoints in a
# deployment that legitimately needs them (production included) without
# requiring a change to a read-only ``.env``: set
# ``TALARIS_ALLOW_PRIVATE_TEE_ENDPOINTS=1`` (or "true"/"yes"/"on") in the
# process environment. When set, it overrides BOTH buckets below uniformly.
_ALLOW_PRIVATE_ENV_VAR = "TALARIS_ALLOW_PRIVATE_TEE_ENDPOINTS"

_RFC1918_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]
_IPV6_ULA_NET = ipaddress.ip_network("fc00::/7")  # unique-local, RFC1918-equivalent


def _explicit_env_override() -> bool | None:
    raw = os.environ.get(_ALLOW_PRIVATE_ENV_VAR)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_rfc1918_or_ula(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in net for net in _RFC1918_NETS)
    return ip in _IPV6_ULA_NET


def _resolve_candidates(host: str) -> list[str]:
    """IP(s) to classify for ``host``, or ``[]`` if it can't be resolved now.

    A literal IP is used directly (no I/O). A hostname is resolved via
    ``socket.getaddrinfo`` (blocking; registration/provisioning are low-rate
    paths so this is acceptable, but it is a DNS round-trip in each). A
    hostname that fails to resolve returns ``[]`` (nothing to classify as
    unsafe) rather than being rejected — this is deliberately lenient so
    unresolvable placeholder hostnames (as used throughout the test suites)
    are not blocked, and it is also the known blind spot: a hostname
    resolving differently at request time than it did here — "DNS
    rebinding" — is NOT caught by this function. Closing that gap fully
    would require re-resolving and pinning the IP at actual connect time in
    the HTTP transport, which this helper does not do.
    """
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    seen: list[str] = []
    for info in infos:
        ip_str = info[4][0].split("%", 1)[0]  # strip IPv6 zone id, if any
        if ip_str not in seen:
            seen.append(ip_str)
    return seen


def validate_tee_endpoint(
    endpoint: str,
    *,
    allow_private: bool | None = None,
    app_env: str | None = None,
) -> None:
    """Raise ``TeeEndpointError`` unless ``endpoint`` is safe for the platform
    to dial.

    Always enforced (no opt-out): scheme must be ``http``/``https`` and a host
    must be present.

    Address-range policy, once the host is resolved to IP(s) (see
    ``_resolve_candidates`` for the unresolvable-hostname / DNS-rebinding
    caveat):

    * link-local (169.254.0.0/16 and the IPv6 equivalent fe80::/10),
      multicast, IETF-reserved, and unspecified (0.0.0.0 / ::) addresses are
      REJECTED UNCONDITIONALLY, unless the explicit
      ``TALARIS_ALLOW_PRIVATE_TEE_ENDPOINTS`` env var (or an explicit
      ``allow_private=True``) opts in. There is no legitimate miner TEE at
      the cloud metadata address or a multicast/reserved address, and this is
      exactly the primitive used to reach it — ``app_env`` never relaxes this
      bucket on its own.
    * loopback (127.0.0.0/8, ::1) and RFC1918-style private ranges
      (10/8, 172.16/12, 192.168/16, and the IPv6 fc00::/7 equivalent) are
      rejected UNLESS ``allow_private`` (explicit, or the env override, or —
      the fallback used when neither is given — ``app_env != "production"``)
      allows them. This is the dev-stack carve-out: this stack legitimately
      registers ``http://127.0.0.1:8801``, and defaulting private/loopback to
      "allowed" outside production keeps that working without touching the
      read-only ``.env``. Production defaults to rejecting them.

    ``app_env`` is accepted as an explicit string (rather than this module
    reading e.g. ``APP_ENV`` from the environment itself) because
    inference-api's own ``app_env`` setting is sourced from pydantic-settings
    (``.env`` file + process env merged), which this shared, process-agnostic
    package has no business re-deriving independently.
    """
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https"):
        raise TeeEndpointError(
            f"tee_endpoint must use http or https, got scheme {parsed.scheme!r}"
        )
    host = parsed.hostname
    if not host:
        raise TeeEndpointError(f"tee_endpoint {endpoint!r} has no host")

    override = _explicit_env_override()
    if allow_private is None:
        allow_private = override if override is not None else (app_env or "local") != "production"
    allow_link_local = override if override is not None else False

    for raw_ip in _resolve_candidates(host):
        ip = ipaddress.ip_address(raw_ip)
        if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            if allow_link_local:
                continue
            raise TeeEndpointError(
                f"tee_endpoint {endpoint!r} resolves to disallowed address "
                f"{raw_ip!r} (link-local/multicast/reserved/unspecified); set "
                f"{_ALLOW_PRIVATE_ENV_VAR}=1 to explicitly allow this"
            )
        if ip.is_loopback or _is_rfc1918_or_ula(ip):
            if allow_private:
                continue
            raise TeeEndpointError(
                f"tee_endpoint {endpoint!r} resolves to a private/loopback address "
                f"{raw_ip!r}; set {_ALLOW_PRIVATE_ENV_VAR}=1 (or run with "
                "app_env != 'production') to explicitly allow this"
            )


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
    "TeeEndpointError",
    "validate_tee_endpoint",
]
