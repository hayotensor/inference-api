"""DocView: a duck-typed read-only view over an attestation document.

The public API accepts EITHER a plain dict (the raw ``GET /attestation`` JSON) OR
anything exposing ``.model_dump()`` (e.g. a ``talaris_contracts.AttestationDocument``).
We deliberately do NOT import talaris_contracts — keeping deps acyclic — and instead
normalize to a dict here, then expose the producer's field names as typed properties.

Field-name mapping to the producer (single source of truth):
  identity_fpr  <- doc["fingerprint"]        (== EnclaveIdentity.fingerprint())
  tls_fpr       <- doc["tls_cert_fingerprint"]
  miner_hash    <- doc["miner"]
  enclave verify key <- doc["public_bundle"]["verify_key"]  (hex ed25519 verify key)

Note: the attestation JSON carries BOTH a top-level ``fingerprint`` (added by the
router from ``identity.fingerprint()``) which is what report_data binds as identity_fpr.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .errors import MalformedDocument


def _as_dict(doc: Any) -> dict:
    """Normalize ``doc`` to a plain dict.

    Accepts a Mapping (used as-is) or anything with a ``model_dump()`` method
    (pydantic-style). Anything else is a malformed input.
    """
    if isinstance(doc, Mapping):
        return dict(doc)
    model_dump = getattr(doc, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
        raise MalformedDocument("model_dump() did not return a mapping")
    raise MalformedDocument(
        "attestation document must be a dict or expose .model_dump()"
    )


class DocView:
    """Read-only, normalized view over an attestation document."""

    __slots__ = ("_d",)

    def __init__(self, doc: Any) -> None:
        self._d = _as_dict(doc)

    @property
    def raw(self) -> dict:
        return self._d

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    # --- identity / mode ---------------------------------------------------

    @property
    def mode(self) -> Optional[str]:
        return self._d.get("mode")

    @property
    def nonce(self) -> str:
        nonce = self._d.get("nonce")
        if not isinstance(nonce, str):
            raise MalformedDocument("document 'nonce' must be a hex string")
        return nonce

    @property
    def identity_fpr(self) -> str:
        """The enclave identity fingerprint bound into report_data as identity_fpr.

        Sourced from the top-level ``fingerprint`` field (router adds it from
        ``identity.fingerprint()``); falls back to public_bundle-derived absence -> error.
        """
        fpr = self._d.get("fingerprint")
        if not isinstance(fpr, str):
            raise MalformedDocument(
                "document 'fingerprint' (identity_fpr) must be a string"
            )
        return fpr

    @property
    def public_bundle(self) -> dict:
        pb = self._d.get("public_bundle")
        if not isinstance(pb, Mapping):
            raise MalformedDocument("document 'public_bundle' must be an object")
        return dict(pb)

    @property
    def verify_key_hex(self) -> str:
        """Enclave Ed25519 verify key (hex) from public_bundle.verify_key."""
        vk = self.public_bundle.get("verify_key")
        if not isinstance(vk, str):
            raise MalformedDocument(
                "public_bundle.verify_key must be a hex string"
            )
        return vk

    @property
    def verify_key_bytes(self) -> bytes:
        try:
            return bytes.fromhex(self.verify_key_hex)
        except ValueError as exc:
            raise MalformedDocument(
                "public_bundle.verify_key is not valid hex"
            ) from exc

    # --- report_data inputs ------------------------------------------------

    @property
    def report_data(self) -> str:
        rd = self._d.get("report_data")
        if not isinstance(rd, str):
            raise MalformedDocument("document 'report_data' must be a hex string")
        return rd

    @property
    def miner_hash(self) -> Optional[str]:
        return self._d.get("miner")

    @property
    def tls_fpr(self) -> Optional[str]:
        return self._d.get("tls_cert_fingerprint")

    @property
    def model_hash(self) -> Optional[str]:
        return self._d.get("model_hash")

    @property
    def usage_chain_head(self) -> Optional[str]:
        # Producer defaults this to "" (never None) but tolerate either.
        return self._d.get("usage_chain_head")

    @property
    def args_hash(self) -> Optional[str]:
        return self._d.get("args_hash")

    @property
    def gpu_hash(self) -> Optional[str]:
        return self._d.get("gpu_hash")

    @property
    def gpu_uuids(self) -> list:
        uuids = self._d.get("gpu_uuids") or []
        if not isinstance(uuids, (list, tuple)):
            raise MalformedDocument("document 'gpu_uuids' must be a list")
        return list(uuids)

    @property
    def usage_count(self) -> Optional[int]:
        return self._d.get("usage_count")

    @property
    def usage_total_tokens(self) -> Optional[int]:
        return self._d.get("usage_total_tokens")

    # --- in-band report_data framing --------------------------------------

    @property
    def report_data_version(self) -> Optional[str]:
        return self._d.get("report_data_version")

    @property
    def report_data_domain_tag(self) -> Optional[str]:
        return self._d.get("report_data_domain_tag")

    @property
    def report_data_fields(self) -> Optional[list]:
        fields = self._d.get("report_data_fields")
        return list(fields) if isinstance(fields, (list, tuple)) else None

    # --- evidence ----------------------------------------------------------

    @property
    def evidence(self) -> dict:
        ev = self._d.get("evidence") or {}
        if not isinstance(ev, Mapping):
            raise MalformedDocument("document 'evidence' must be an object")
        return dict(ev)

    @property
    def dev_signature_hex(self) -> Optional[str]:
        """Enclave Ed25519 signature over report_data (DEV mode only)."""
        sig = self.evidence.get("dev_signature")
        return sig if isinstance(sig, str) else None

    @property
    def tls_pubkey_signature(self) -> Optional[str]:
        """Enclave Ed25519 signature over TLS_SPKI tag + served cert SPKI DER (hex)."""
        return self._d.get("tls_pubkey_signature")
