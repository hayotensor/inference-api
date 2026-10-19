"""Enclave identity binding: public_bundle -> fingerprint.

report_data commits only to ``identity_fpr`` (the document's top-level ``fingerprint``).
The keys a relying party actually USES live in ``public_bundle`` (Ed25519 verify key,
X25519 sealing key, and on E2EE-capable enclaves the HPKE public key). Unless the
verifier recomputes the fingerprint from those keys, a relay can keep a genuine
fingerprint/report_data and swap any key in public_bundle for its own.

Mirrors ``tee_wrapper.security.identity.EnclaveIdentity.fingerprint`` byte-for-byte:

    sha256(verify_key(32) || sealing_key(32) [|| e2ee_key(1216)])

All three keys are fixed-length, so plain concatenation is unambiguous. ``e2ee_key`` is
absent on enclaves that predate E2EE; the two-key form is then the original v6 value.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .docview import DocView
from .errors import MalformedDocument

VERIFY_KEY_LEN = 32
SEALING_KEY_LEN = 32
# ML-KEM-768 public key (1184) || X25519 public key (32): the HPKE MLKEM768-X25519 key.
E2EE_KEY_LEN = 1216


def _key_bytes(bundle: dict, name: str, length: int) -> bytes:
    value = bundle.get(name)
    if not isinstance(value, str):
        raise MalformedDocument(f"public_bundle.{name} must be a hex string")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise MalformedDocument(f"public_bundle.{name} is not valid hex") from exc
    if len(raw) != length:
        raise MalformedDocument(
            f"public_bundle.{name} must be {length} bytes, got {len(raw)}"
        )
    return raw


def bundle_e2ee_key(view: DocView) -> Optional[bytes]:
    """The enclave's E2EE public key from public_bundle, or None if it has none."""
    bundle = view.public_bundle
    if bundle.get("e2ee_key") is None:
        return None
    return _key_bytes(bundle, "e2ee_key", E2EE_KEY_LEN)


def recompute_fingerprint(view: DocView) -> str:
    """Recompute the identity fingerprint from the document's public_bundle."""
    bundle = view.public_bundle
    h = hashlib.sha256()
    h.update(_key_bytes(bundle, "verify_key", VERIFY_KEY_LEN))
    h.update(_key_bytes(bundle, "sealing_key", SEALING_KEY_LEN))
    e2ee = bundle_e2ee_key(view)
    if e2ee is not None:
        h.update(e2ee)
    return h.hexdigest()


__all__ = [
    "E2EE_KEY_LEN",
    "bundle_e2ee_key",
    "recompute_fingerprint",
]
