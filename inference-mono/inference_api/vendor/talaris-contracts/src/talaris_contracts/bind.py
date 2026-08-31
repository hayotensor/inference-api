"""Bind wire format.

Authoritative source: ``tee_wrapper.routers.attestation.BindRequest`` and the
``bind_message`` format in ``tee_wrapper.security.miner``.

POST /bind body = {miner_pubkey, signature, key_type?}. The miner proves control
of its hotkey by signing ``bind_message(boot_nonce, fingerprint)``:

    BIND_DOMAIN_TAG + bytes.fromhex(boot_nonce_hex) + fingerprint.encode()

where ``BIND_DOMAIN_TAG = b"tee-wrapper/bind/v1"``, ``boot_nonce``/``fingerprint``
are the values published in the attestation document.

``key_type`` is DEPRECATED on the wire: the enclave uses its configured
``miner_key_type`` (sr25519 by default, ed25519 supported) and ignores the body's
value so a caller cannot downgrade/swap the verification algorithm.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .version import BIND_DOMAIN_TAG, RECEIPTS_AUTH_DOMAIN_TAG


class BindRequest(BaseModel):
    """POST /bind request body.

    Mirrors ``tee_wrapper.routers.attestation.BindRequest``:
    - ``miner_pubkey``: hex miner hotkey public key.
    - ``signature``: hex signature over ``bind_message(boot_nonce, fingerprint)``.
    - ``key_type``: optional, DEPRECATED (not authoritative; enclave uses its own).
    """

    model_config = ConfigDict(extra="allow")

    miner_pubkey: str
    signature: str
    key_type: str | None = None


def bind_message(boot_nonce_hex: str, fingerprint: str) -> bytes:
    """Byte-exact mirror of ``tee_wrapper.security.miner.bind_message``.

    The message a miner signs to prove control of its hotkey and consent to this
    enclave::

        b"tee-wrapper/bind/v1" + bytes.fromhex(boot_nonce_hex) + fingerprint.encode()
    """
    return BIND_DOMAIN_TAG + bytes.fromhex(boot_nonce_hex) + fingerprint.encode()


def receipts_auth_message(timestamp: int, path: str) -> bytes:
    """Byte-exact mirror of ``tee_wrapper.security.miner.receipts_auth_message``.

    The canonical, domain-separated message a BOUND miner signs to authenticate a pull
    of ``/usage/receipts`` (decision #9). The miner node — which does NOT hold the
    enclave admin token — proves liveness with a signature over this message, verified
    by the enclave against the bound miner verify key with its configured
    ``miner_key_type`` (sr25519 by default, ed25519 supported).

    Wire format (domain-tagged; ``timestamp`` is a FIXED-WIDTH 8-byte big-endian u64 so
    the timestamp and path fields can never run together / be ambiguously re-split)::

        b"tee-wrapper/receipts-auth/v1" + u64_be(timestamp) + path.encode()

    where ``timestamp`` is unix seconds (the value sent in the ``X-Miner-Timestamp``
    header) and ``path`` is the exact request path being authorized (e.g.
    ``"/usage/receipts"``). The caller sends the proof as headers::

        X-Miner-Timestamp: <unix seconds>
        X-Miner-Signature: <hex signature over receipts_auth_message(ts, path)>
    """
    if timestamp < 0:
        raise ValueError("timestamp must be a non-negative unix-seconds integer")
    # to_bytes(8, ...) raises OverflowError for timestamps that don't fit a u64; that
    # is the desired fail-closed behaviour for a nonsensical timestamp.
    return RECEIPTS_AUTH_DOMAIN_TAG + int(timestamp).to_bytes(8, "big") + path.encode()


__all__ = [
    "BindRequest",
    "bind_message",
    "BIND_DOMAIN_TAG",
    "receipts_auth_message",
    "RECEIPTS_AUTH_DOMAIN_TAG",
]
