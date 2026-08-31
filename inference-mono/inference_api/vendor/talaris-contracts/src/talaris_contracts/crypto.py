"""Crypto helpers that reproduce the producer's seal + sign flows byte-for-byte.

Authoritative reference flow (``tee_wrapper/tests/test_provision.py``)::

    box = SealedBox(PublicKey(bytes.fromhex(sealing_pub_hex)))
    sealed = box.encrypt(json.dumps(creds).encode())
    signature = validator_sk.sign(sealed).signature.hex()

and the root-signed validator list (``ValidatorAuthorizer._verify_and_extract``)::

    message = canonical_list_message(validators, version, not_before)
    signature = root_sk.sign(message).signature.hex()

The seal step is non-deterministic (SealedBox uses an ephemeral key), so a
helper that re-seals will not byte-match a prior ciphertext; what is invariant is
that the enclave can unseal+json.loads back to the original creds, and that the
signature verifies over whatever sealed bytes were produced.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from nacl.exceptions import BadSignatureError
from nacl.public import PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey

from .allowlist import AllowlistEntry, ModelAllowlistArtifact, canonical_allowlist_message
from .miner_stats import (
    MinerEpochStatsArtifact,
    MinerStatsEntry,
    canonical_miner_stats_message,
)
from .provision import Credentials, ProvisionRequest
from .validator_list import ValidatorList, canonical_list_message

__all__ = [
    "seal_credentials",
    "build_provision_request",
    "sign_validator_list",
    "sign_model_allowlist",
    "verify_model_allowlist",
    "sign_miner_stats",
    "verify_miner_stats",
    "miner_hash",
]


def _creds_to_dict(creds: Credentials | Mapping[str, Any]) -> dict[str, Any]:
    """Normalize credentials to the exact JSON dict the enclave expects.

    The enclave reads ``admin_token`` (required) and ``inference_keys`` (optional)
    after ``json.loads``. A plain Credentials model serializes to exactly those
    keys (plus any extra="allow" fields the caller added).
    """
    if isinstance(creds, Credentials):
        return creds.model_dump()
    return dict(creds)


def seal_credentials(
    creds: Credentials | Mapping[str, Any], enclave_sealing_pub_hex: str
) -> bytes:
    """Seal credentials to the enclave's X25519 sealing public key.

    Reproduces the producer flow exactly::

        SealedBox(PublicKey(bytes.fromhex(enclave_sealing_pub_hex)))
            .encrypt(json.dumps(creds).encode())

    Returns the raw sealed-box ciphertext bytes (hex-encode for the wire). The
    enclave recovers the creds with ``json.loads(SealedBox(priv).decrypt(...))``.
    """
    box = SealedBox(PublicKey(bytes.fromhex(enclave_sealing_pub_hex)))
    payload = json.dumps(_creds_to_dict(creds)).encode()
    return box.encrypt(payload)


def build_provision_request(
    creds: Credentials | Mapping[str, Any],
    enclave_sealing_pub_hex: str,
    platform_signing_key: SigningKey,
) -> ProvisionRequest:
    """Build a fully-formed POST /provision body.

    Seals ``creds`` to the enclave sealing key, signs the raw sealed bytes with
    the platform/validator Ed25519 ``platform_signing_key``, and returns a
    ``ProvisionRequest`` with hex-encoded ``sealed`` and ``signature`` and the
    signer's verify key as ``validator_pubkey``.

    Mirrors ``tee_wrapper/tests/test_provision.py``: the enclave verifies the
    signature over the raw sealed bytes, then unseals + json.loads the creds.
    """
    sealed = seal_credentials(creds, enclave_sealing_pub_hex)
    signature = platform_signing_key.sign(sealed).signature
    return ProvisionRequest(
        validator_pubkey=bytes(platform_signing_key.verify_key).hex(),
        sealed=sealed.hex(),
        signature=signature.hex(),
    )


def sign_validator_list(
    validators: list[str],
    version: int,
    root_key: SigningKey,
    not_before: int | None = None,
) -> ValidatorList:
    """Sign a validator list with the root Ed25519 key.

    Signs ``canonical_list_message(validators, version, not_before)`` and returns
    a ``ValidatorList`` carrying the (verbatim) validators, version, hex
    signature, and optional ``not_before``. The signature verifies against
    ``ValidatorAuthorizer._verify_and_extract``, which recomputes the same
    canonical message and checks it with the baked-in root verify key.

    Note: the producer sorts/lowercases keys only inside the canonical message,
    so the verifier will reproduce the same sorted set regardless of the input
    order. The returned ``validators`` list preserves the caller's input.
    """
    message = canonical_list_message(validators, version, not_before)
    signature = root_key.sign(message).signature
    return ValidatorList(
        validators=list(validators),
        version=int(version),
        signature=signature.hex(),
        not_before=not_before,
    )


def sign_model_allowlist(
    entries,
    version: int,
    signing_key: SigningKey,
    not_before: int | None = None,
) -> ModelAllowlistArtifact:
    """Sign a model allowlist with the platform Ed25519 key.

    Signs ``canonical_allowlist_message(entries, version, not_before)`` and returns
    a ``ModelAllowlistArtifact`` carrying the entries, version, hex signature, the
    optional ``not_before``, and the signer's verify key (hex). ``entries`` may be
    ``AllowlistEntry`` instances, mappings, or attribute objects — the canonical
    message normalizes either way, so only the SIGNED BYTES matter for verification.
    """
    message = canonical_allowlist_message(entries, version, not_before)
    signature = signing_key.sign(message).signature
    return ModelAllowlistArtifact(
        entries=[_coerce_allowlist_entry(e) for e in entries],
        version=int(version),
        signature=signature.hex(),
        not_before=not_before,
        signer_pubkey=signing_key.verify_key.encode().hex(),
    )


def verify_model_allowlist(artifact: ModelAllowlistArtifact, verify_key_hex: str) -> bool:
    """Verify a ``ModelAllowlistArtifact`` against a PINNED platform verify key.

    Recomputes ``artifact.canonical_message()`` and checks the hex Ed25519
    signature. Returns False (never raises) on a bad signature or malformed key.
    Callers MUST additionally enforce ``version`` monotonicity (rollback) and any
    ``not_before`` freshness gate.
    """
    try:
        VerifyKey(bytes.fromhex(verify_key_hex)).verify(
            artifact.canonical_message(), bytes.fromhex(artifact.signature)
        )
        return True
    except (BadSignatureError, ValueError):
        return False


def sign_miner_stats(
    epoch: int,
    entries,
    version: int,
    signing_key: SigningKey,
    not_before: int | None = None,
) -> MinerEpochStatsArtifact:
    """Sign one epoch's miner statistics with the platform Ed25519 key.

    Signs ``canonical_miner_stats_message(epoch, entries, version, not_before)`` and
    returns a ``MinerEpochStatsArtifact``. ``entries`` may be ``MinerStatsEntry``
    instances, mappings, or attribute objects (e.g. ORM rows) — the canonical
    message normalizes either way, so only the SIGNED BYTES matter for verification.

    The same platform key signs the model allowlist; the ``kind`` discriminator in
    each canonical message keeps the two signatures non-interchangeable.
    """
    message = canonical_miner_stats_message(epoch, entries, version, not_before)
    signature = signing_key.sign(message).signature
    return MinerEpochStatsArtifact(
        epoch=int(epoch),
        entries=[_coerce_miner_stats_entry(e) for e in entries],
        version=int(version),
        signature=signature.hex(),
        not_before=not_before,
        signer_pubkey=signing_key.verify_key.encode().hex(),
    )


def verify_miner_stats(artifact: MinerEpochStatsArtifact, verify_key_hex: str) -> bool:
    """Verify a ``MinerEpochStatsArtifact`` against a PINNED platform verify key.

    Recomputes ``artifact.canonical_message()`` and checks the hex Ed25519
    signature. Returns False (never raises) on a bad signature or malformed key.
    Callers MUST additionally enforce any ``not_before`` freshness gate and the
    per-epoch immutability rule (never replace an already-accepted epoch artifact).
    """
    try:
        VerifyKey(bytes.fromhex(verify_key_hex)).verify(
            artifact.canonical_message(), bytes.fromhex(artifact.signature)
        )
        return True
    except (BadSignatureError, ValueError):
        return False


def _coerce_miner_stats_entry(entry) -> MinerStatsEntry:
    if isinstance(entry, MinerStatsEntry):
        return entry
    if isinstance(entry, Mapping):
        return MinerStatsEntry.model_validate(dict(entry))
    return MinerStatsEntry.model_validate(entry, from_attributes=True)


def _coerce_allowlist_entry(entry) -> AllowlistEntry:
    if isinstance(entry, AllowlistEntry):
        return entry
    if isinstance(entry, Mapping):
        return AllowlistEntry.model_validate(dict(entry))
    return AllowlistEntry.model_validate(entry, from_attributes=True)


def miner_hash(pubkey_hex: str) -> str:
    """Byte-exact mirror of ``tee_wrapper.security.miner.miner_hash``.

    ``sha256(bytes.fromhex(pubkey_hex)).hexdigest()`` — the miner identifier bound
    into report_data and reported by the enclave after a successful /bind.
    """
    return hashlib.sha256(bytes.fromhex(pubkey_hex)).hexdigest()
