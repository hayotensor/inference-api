"""Platform-signed per-epoch miner statistics wire format.

The platform (inference-api) is the only party that sees every inference request:
it selects the miner, forwards the request, and settles the usage. Once a subnet
epoch has closed it FINALIZES those records into a per-``(hotkey, model)``
aggregate, signs the canonical artifact with the platform Ed25519 key, and serves
it verbatim forever after. Every subnet validator fetches that artifact, verifies
it against the PINNED platform key, and scores miners from it.

WHY THIS SHAPE (consensus-safety)
---------------------------------
``subnet.consensus.utils.compare_consensus_data`` requires validators to submit an
EXACTLY matching score set. Statistics that reach validators by gossip (the
now-removed merkle-DAG usage batches) propagate with lag, so no two validators
ever hold the same set at scoring time — which is what forced the old
membership-only gate at a flat score. An epoch-finalized, IMMUTABLE, signed
artifact removes that skew entirely: every validator scores byte-identical input,
so numeric volume-weighted scores become deterministic.

Two properties carry that guarantee and must not be weakened:

1. **Immutability.** A finalized ``(epoch)`` artifact is built once and re-served
   byte-for-byte. Rebuilding it with different numbers would silently split
   validators. Consumers reject a second, differing artifact for an epoch they
   have already accepted.
2. **Canonical bytes.** :func:`canonical_miner_stats_message` is the single source
   of truth for the signed bytes. The signer (inference-api) and every verifier
   (validators) MUST go through it; never re-serialize entries by hand.

Mirrors the ``allowlist`` / ``validator_list`` pattern: canonical, sorted, compact
JSON signed with a hex Ed25519 signature, a monotonic ``version``, and an optional
``not_before`` freshness gate. The ``kind`` discriminator domain-separates this
signature from the allowlist and validator-list signatures so none can ever be
replayed as another.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict

MINER_STATS_KIND = "miner-stats/v1"


class MinerStatsEntry(BaseModel):
    """One miner's finalized statistics for one model over one epoch.

    Identity: ``hotkey`` is the on-chain subnet-node hotkey (the trust anchor that
    ties these statistics to a chain node); ``subnet_node_id`` is carried so a
    validator can score without a second chain lookup, but it MUST still be
    cross-checked against the chain's own node list — never trusted blindly.

    Counters:
    - ``requests_ok`` / ``requests_failed``: forward attempts the platform observed.
      A failed attempt is a real signal (the miner accepted routing and did not
      serve), which is why failures are counted rather than dropped.
    - ``verified_prompt_tokens`` / ``verified_completion_tokens`` /
      ``verified_total_tokens``: tokens present in BOTH the platform's own records
      AND the enclave's signed usage chain. Only these feed scoring — a number the
      platform cannot inflate alone and the miner cannot inflate alone.
    - ``latency_p95_ms``: platform-measured, so it cannot be self-reported.
    - ``usage_count_end`` / ``usage_chain_head_end``: the enclave's cumulative,
      tamper-evident usage anchor at the end of the epoch, carried verbatim so a
      validator can cross-check it against the counters its own attestation probe
      independently observed.
    """

    model_config = ConfigDict(extra="allow")

    hotkey: str
    subnet_node_id: int | None = None
    model_id: str
    model_version: str | None = None
    requests_ok: int = 0
    requests_failed: int = 0
    verified_prompt_tokens: int = 0
    verified_completion_tokens: int = 0
    verified_total_tokens: int = 0
    latency_p95_ms: int | None = None
    usage_count_end: int | None = None
    usage_chain_head_end: str | None = None


def _entry_fields(entry: Any) -> dict[str, Any]:
    """Normalize an entry (mapping OR object) to the canonical field map.

    Optional fields collapse to a fixed default (``""`` / ``0``) rather than being
    omitted, so an entry that carries ``None`` and one that omits the field sign to
    identical bytes. ``hotkey`` and the chain head are lowercased because they are
    hex/address values whose case is not semantically meaningful.
    """
    get = entry.get if isinstance(entry, Mapping) else (lambda k: getattr(entry, k, None))

    def _int(key: str) -> int:
        value = get(key)
        return 0 if value is None else int(value)

    return {
        "hotkey": str(get("hotkey") or "").lower(),
        "subnet_node_id": _int("subnet_node_id"),
        "model_id": str(get("model_id") or ""),
        "model_version": str(get("model_version") or ""),
        "requests_ok": _int("requests_ok"),
        "requests_failed": _int("requests_failed"),
        "verified_prompt_tokens": _int("verified_prompt_tokens"),
        "verified_completion_tokens": _int("verified_completion_tokens"),
        "verified_total_tokens": _int("verified_total_tokens"),
        "latency_p95_ms": _int("latency_p95_ms"),
        "usage_count_end": _int("usage_count_end"),
        "usage_chain_head_end": str(get("usage_chain_head_end") or "").lower(),
    }


def canonical_miner_stats_message(
    epoch: int, entries, version: int, not_before: int | None = None
) -> bytes:
    """Deterministic bytes the platform key signs to authenticate an epoch's stats.

    Entries are normalized (``None`` -> ``""``/``0``, hotkey and chain head
    lowercased) and sorted by ``(hotkey, model_id, model_version)`` so input order
    is irrelevant; the body is ``separators=(",", ":")`` compact JSON with
    ``sort_keys=True``, UTF-8 encoded. ``epoch`` is bound in so an artifact for one
    epoch can never be replayed as another, and the monotonic ``version`` (plus
    optional ``not_before``) prevents replaying a stale-but-validly-signed build.
    """
    norm = [_entry_fields(e) for e in entries]
    norm.sort(key=lambda d: (d["hotkey"], d["model_id"], d["model_version"]))
    body: dict[str, Any] = {
        "kind": MINER_STATS_KIND,
        "epoch": int(epoch),
        "version": int(version),
        "entries": norm,
    }
    if not_before is not None:
        body["not_before"] = int(not_before)
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


class MinerEpochStatsArtifact(BaseModel):
    """A platform-signed epoch statistics snapshot as fetched/served on the wire.

    ``signature`` is a hex Ed25519 signature by the platform key over
    ``canonical_miner_stats_message(epoch, entries, version, not_before)``.
    ``signer_pubkey`` is carried for convenience/diagnostics, but verifiers MUST
    check against their own PINNED platform key, not this field.
    """

    model_config = ConfigDict(extra="allow")

    epoch: int
    entries: list[MinerStatsEntry]
    version: int
    signature: str
    not_before: int | None = None
    signer_pubkey: str | None = None

    def canonical_message(self) -> bytes:
        """The exact bytes the ``signature`` should verify against."""
        return canonical_miner_stats_message(
            self.epoch, self.entries, self.version, self.not_before
        )


__all__ = [
    "MINER_STATS_KIND",
    "MinerEpochStatsArtifact",
    "MinerStatsEntry",
    "canonical_miner_stats_message",
]
