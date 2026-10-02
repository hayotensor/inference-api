"""Platform-signed per-inference attestation receipts (HYP-25 / HYP-38).

Each inference response served by a real attested miner is accompanied by a
compact, INDEPENDENTLY-VERIFIABLE receipt: a canonical (compact-JSON,
``sort_keys``) message binding the specific request to the serving enclave's
most recently VERIFIED attestation (model_hash / enclave TEE verify key / TLS
leaf fingerprint / attestation mode + timestamp), signed with the SAME
platform Ed25519 key that signs the model allowlist (``GET /allowlist``) and
the per-epoch miner statistics (``GET /subnet/stats/*``) -- see
``inference_api.provisioner.key.load_provisioner_signing_key``.

A third party fetches the PINNED platform verify key from ``GET /attest``
(never trusting the ``signer_pubkey`` carried on the receipt itself) and
verifies the signature with nothing more than PyNaCl + this module's
canonical-message function -- no access to the platform's database or
internal state is required. This mirrors
``talaris_contracts.allowlist.canonical_allowlist_message`` byte-for-byte in
style (compact JSON, ``sort_keys=True``) so the same verification mental model
applies to every artifact this platform signs.

This does NOT re-deliver a raw hardware quote per request: the TEE hardware
quote was already checked, once, by ``talaris_attest.verify_attestation``, at
provisioning time (see ``inference_api/provisioner/service.py``). What this
adds is a fresh, per-request cryptographic statement, in the platform's own
voice, naming EXACTLY which attested enclave (by its own attested identity:
verify key + model_hash + TLS fingerprint) produced this specific response, so
a caller does not have to take ``"attested": true`` on faith.
"""

from __future__ import annotations

import json
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from inference_api.schemas import AttestationReceipt

INFERENCE_RECEIPT_KIND = "inference-receipt/v1"


def canonical_inference_receipt_message(
    *,
    request_id: str,
    model_id: str,
    model_hash: str | None,
    enclave_verify_key: str | None,
    tls_cert_fingerprint: str | None,
    miner_hash: str | None,
    attestation_mode: str | None,
    attestation_verified_at: int | None,
) -> bytes:
    """Deterministic bytes the platform key signs over one inference receipt.

    Every field is normalized to a string (``None`` -> ``""``) except the
    integer timestamp, so verification never depends on Python-side type
    coercion -- a verifier in any language reproduces the same bytes from the
    receipt's own field values.
    """
    body: dict[str, Any] = {
        "kind": INFERENCE_RECEIPT_KIND,
        "request_id": str(request_id),
        "model_id": str(model_id),
        "model_hash": str(model_hash or ""),
        "enclave_verify_key": str(enclave_verify_key or ""),
        "tls_cert_fingerprint": str(tls_cert_fingerprint or ""),
        "miner_hash": str(miner_hash or ""),
        "attestation_mode": str(attestation_mode or ""),
        "attestation_verified_at": attestation_verified_at,
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def sign_inference_receipt(
    *,
    request_id: str,
    model_id: str,
    model_hash: str | None,
    enclave_verify_key: str | None,
    tls_cert_fingerprint: str | None,
    miner_hash: str | None,
    attestation_mode: str | None,
    attestation_verified_at: int | None,
    signing_key: SigningKey,
) -> AttestationReceipt:
    """Sign one inference receipt with the platform Ed25519 key."""
    message = canonical_inference_receipt_message(
        request_id=request_id,
        model_id=model_id,
        model_hash=model_hash,
        enclave_verify_key=enclave_verify_key,
        tls_cert_fingerprint=tls_cert_fingerprint,
        miner_hash=miner_hash,
        attestation_mode=attestation_mode,
        attestation_verified_at=attestation_verified_at,
    )
    signature = signing_key.sign(message).signature
    return AttestationReceipt(
        kind=INFERENCE_RECEIPT_KIND,
        request_id=request_id,
        model_id=model_id,
        model_hash=model_hash,
        enclave_verify_key=enclave_verify_key,
        tls_cert_fingerprint=tls_cert_fingerprint,
        miner_hash=miner_hash,
        attestation_mode=attestation_mode,
        attestation_verified_at=attestation_verified_at,
        signature=signature.hex(),
        signer_pubkey=signing_key.verify_key.encode().hex(),
    )


def verify_inference_receipt(receipt: AttestationReceipt, verify_key_hex: str) -> bool:
    """Verify ``receipt`` against a PINNED platform verify key.

    Callers MUST pass a key pinned out of band (e.g. fetched once from
    ``GET /attest`` and cached) -- never ``receipt.signer_pubkey``, which is
    self-asserted and carried for convenience/diagnostics only. Returns False
    (never raises) on a bad signature or malformed input.
    """
    try:
        message = canonical_inference_receipt_message(
            request_id=receipt.request_id,
            model_id=receipt.model_id,
            model_hash=receipt.model_hash,
            enclave_verify_key=receipt.enclave_verify_key,
            tls_cert_fingerprint=receipt.tls_cert_fingerprint,
            miner_hash=receipt.miner_hash,
            attestation_mode=receipt.attestation_mode,
            attestation_verified_at=receipt.attestation_verified_at,
        )
        VerifyKey(bytes.fromhex(verify_key_hex)).verify(
            message, bytes.fromhex(receipt.signature)
        )
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


__all__ = [
    "INFERENCE_RECEIPT_KIND",
    "canonical_inference_receipt_message",
    "sign_inference_receipt",
    "verify_inference_receipt",
]
