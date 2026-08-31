"""Platform-signed model allowlist wire format.

The platform (inference-api) signs the canonical set of approved
``(model_id -> model_hash[, args_hash, gpu_hash])`` entries; every subnet
validator verifies it against the PINNED platform Ed25519 key and feeds the SAME
``talaris_attest.ModelAllowlistEntry`` set into per-model attestation
verification. That keeps per-model membership identical across validators, so the
exact-match consensus comparison (``subnet.consensus.utils.compare_consensus_data``)
stays deterministic.

Mirrors the ``validator_list`` pattern: a canonical, sorted, compact-JSON message
signed with a hex Ed25519 signature, a monotonic ``version`` for rollback
protection, and an optional ``not_before`` freshness gate. The ``kind``
discriminator domain-separates this signature from the validator-list signature so
one can never be replayed as the other.

CRITICAL: ``canonical_allowlist_message`` is the single source of truth for the
signed bytes — the signer (inference-api) and every verifier (subnet validators)
MUST go through it; never re-serialize the entries by hand.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

ALLOWLIST_KIND = "model-allowlist/v1"


class AllowlistEntry(BaseModel):
    """One approved model artifact. ``model_id``/``model_version`` identify WHICH
    model; ``model_hash`` (+ optional ``args_hash``/``gpu_hash`` pins) is what the
    enclave must attest. Maps to ``talaris_attest.ModelAllowlistEntry`` (which drops
    the id/version) when grouped by model for verification."""

    model_config = ConfigDict(extra="allow")

    model_id: str
    model_version: str | None = None
    model_hash: str
    args_hash: str | None = None
    gpu_hash: str | None = None
    label: str | None = None


def _entry_fields(entry: Any) -> dict[str, str]:
    """Normalize an entry (mapping OR object) to the canonical string field map."""
    get = entry.get if isinstance(entry, Mapping) else (lambda k: getattr(entry, k, None))
    return {
        "model_id": str(get("model_id")),
        "model_version": str(get("model_version") or ""),
        "model_hash": str(get("model_hash") or "").lower(),
        "args_hash": str(get("args_hash") or ""),
        "gpu_hash": str(get("gpu_hash") or ""),
        "label": str(get("label") or ""),
    }


def canonical_allowlist_message(entries, version: int, not_before: int | None = None) -> bytes:
    """Deterministic bytes the platform key signs to authenticate an allowlist.

    Entries are normalized (None -> "", model_hash lowercased) and sorted by
    ``(model_id, model_version, model_hash)`` so input order is irrelevant; the body
    is ``separators=(",", ":")`` compact JSON with ``sort_keys=True``, UTF-8 encoded.
    A monotonic ``version`` (and optional ``not_before``) is bound so an old but
    validly-signed snapshot cannot be replayed.
    """
    norm = [_entry_fields(e) for e in entries]
    norm.sort(key=lambda d: (d["model_id"], d["model_version"], d["model_hash"]))
    body: dict[str, Any] = {"kind": ALLOWLIST_KIND, "version": int(version), "entries": norm}
    if not_before is not None:
        body["not_before"] = int(not_before)
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


class ModelAllowlistArtifact(BaseModel):
    """A platform-signed allowlist as fetched/served on the wire.

    ``signature`` is a hex Ed25519 signature by the platform key over
    ``canonical_allowlist_message(entries, version, not_before)``. ``signer_pubkey``
    is carried for convenience/diagnostics, but verifiers MUST check against their
    own PINNED platform key, not this field.
    """

    model_config = ConfigDict(extra="allow")

    entries: list[AllowlistEntry]
    version: int
    signature: str
    not_before: int | None = None
    signer_pubkey: str | None = None

    def canonical_message(self) -> bytes:
        """The exact bytes the ``signature`` should verify against."""
        return canonical_allowlist_message(self.entries, self.version, self.not_before)


__all__ = [
    "ALLOWLIST_KIND",
    "AllowlistEntry",
    "ModelAllowlistArtifact",
    "canonical_allowlist_message",
]
