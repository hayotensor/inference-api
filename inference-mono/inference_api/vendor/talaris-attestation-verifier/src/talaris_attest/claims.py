"""Caller-supplied policy for what a *valid* attestation must claim.

ExpectedClaims is the verifier's policy object. Its DEFAULTS are PRODUCTION-SAFE:
  * required_modes == {"nvidia"}  -> only hardware-attested docs pass the mode gate.
  * allow_insecure_dev == False   -> dev docs are rejected, and the stub backends are
                                     refused (the second, independent dev gate).
  * require_gpu_evidence / require_tdx_quote == True -> hardware evidence is mandatory.

A test/dev caller must OPT IN to the insecure path by setting allow_insecure_dev=True
(and typically required_modes={"dev"} and the stub backends).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid import cycle at runtime
    from .backends.base import GpuEvidenceBackend, TdxQuoteBackend


@dataclass(frozen=True)
class ModelAllowlistEntry:
    """One allowed (model, [args], [gpu]) tuple.

    model_hash:  REQUIRED — must equal doc.model_hash for this entry to match.
    args_hash:   optional pin — when set, doc.args_hash must also equal it.
    gpu_hash:    optional pin — when set, doc.gpu_hash must also equal it.
    label:       optional human label for diagnostics.
    """

    model_hash: str
    args_hash: Optional[str] = None
    gpu_hash: Optional[str] = None
    label: Optional[str] = None

    def matches(self, model_hash: Optional[str], args_hash: Optional[str],
                gpu_hash: Optional[str]) -> bool:
        if model_hash is None or model_hash != self.model_hash:
            return False
        if self.args_hash is not None and args_hash != self.args_hash:
            return False
        if self.gpu_hash is not None and gpu_hash != self.gpu_hash:
            return False
        return True


@dataclass(frozen=True)
class FreshnessPolicy:
    """Optional freshness / monotonicity policy.

    The nonce echo (step 2) is the primary freshness control. This policy carries
    optional monotonic high-water marks a long-lived validator tracks across polls
    (usage_count / usage_total_tokens never regress; usage_chain_head advances). When a
    bound is None the corresponding check is skipped. These are advisory and recorded as
    informational checks unless ``required`` is set.
    """

    min_usage_count: Optional[int] = None
    min_usage_total_tokens: Optional[int] = None
    required: bool = False


@dataclass
class ExpectedClaims:
    """Caller policy. Defaults are production-safe (see module docstring)."""

    # --- mode gate (first dev gate) ---------------------------------------
    required_modes: frozenset[str] = frozenset({"nvidia"})
    allow_insecure_dev: bool = False

    # --- hardware-evidence requirements -----------------------------------
    require_gpu_evidence: bool = True
    require_tdx_quote: bool = True

    # --- pluggable backends -----------------------------------------------
    # When None, a default is chosen by verify(): production -> real backends
    # (nvidia_nras / intel_pcs); insecure-dev -> stub backends. A stub backend is
    # ALWAYS refused in production via the backend_insecure_gate check.
    nvidia_backend: "Optional[GpuEvidenceBackend]" = None
    tdx_backend: "Optional[TdxQuoteBackend]" = None

    # --- allowlists / expected values -------------------------------------
    model_allowlist: Optional[list[ModelAllowlistEntry]] = None
    expected_miner_hash: Optional[str] = None

    # --- freshness --------------------------------------------------------
    freshness: FreshnessPolicy = field(default_factory=FreshnessPolicy)

    # --- TLS pin ----------------------------------------------------------
    # Optional cert DER supplied by an offline caller so the TLS-pin step can run without
    # a live connection. build_pinned_client checks the served cert live instead.
    served_cert_der: Optional[bytes] = None

    def is_insecure_dev(self) -> bool:
        return bool(self.allow_insecure_dev)
