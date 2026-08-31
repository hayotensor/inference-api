"""Production-policy factory: the SINGLE source of truth for how callers construct an
``ExpectedClaims`` for attestation verification.

Both planes that verify attestation (inference-api's provisioner and the inference-subnet
validators) MUST build their policy through ``build_expected_claims`` so they cannot
diverge on what "production" means (which modes are required, which real backends run,
whether dev is permitted).

  allow_dev=True  -> DEV/TEST policy: required_modes={"dev"}, allow_insecure_dev=True.
                     verify_attestation auto-selects the stub backends. NEVER in prod.
  allow_dev=False -> PRODUCTION policy: required_modes={"nvidia"}, allow_insecure_dev=False,
                     with the REAL NvidiaNras + IntelPcs backends constructed from config.
                     Anything unverified fails closed.

Only config values that are explicitly provided override the backends' own (production-safe)
defaults — e.g. an unset ``nvidia.expected_issuer`` keeps the backend default rather than
being clobbered with None.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .backends.intel_pcs import IntelPcsTdxBackend
from .backends.nvidia_nras import NvidiaNrasBackend
from .claims import ExpectedClaims, ModelAllowlistEntry

__all__ = ["NvidiaPolicy", "IntelPolicy", "build_expected_claims"]


@dataclass(frozen=True)
class NvidiaPolicy:
    """NVIDIA NRAS verification config (None fields keep the backend default)."""

    jwks_url: Optional[str] = None
    expected_issuer: Optional[str] = None
    nvidia_root_pem: Optional[str] = None
    nras_url: Optional[str] = None
    allowed_algs: Optional[tuple[str, ...]] = None


@dataclass(frozen=True)
class IntelPolicy:
    """Intel TDX/PCS verification config.

    ``trusted_root_pem=None`` keeps the bundled real Intel SGX Root CA pin; ``pcs_base`` /
    ``allowed_tcb_statuses=None`` keep the backend defaults. ``require_collateral`` defaults
    True (production-safe).
    """

    trusted_root_pem: Optional[str] = None
    pcs_base: Optional[str] = None
    allowed_tcb_statuses: Optional[frozenset] = None
    require_collateral: bool = True


def build_expected_claims(
    *,
    allow_dev: bool,
    expected_miner_hash: Optional[str] = None,
    model_allowlist: Optional[list[ModelAllowlistEntry]] = None,
    nvidia: Optional[NvidiaPolicy] = None,
    intel: Optional[IntelPolicy] = None,
    require_gpu_evidence: bool = True,
    require_tdx_quote: bool = True,
) -> ExpectedClaims:
    """Build the verification policy. See module docstring for the dev vs production split."""
    if allow_dev:
        # DEV/TEST: stub backends are auto-selected by verify_attestation under
        # allow_insecure_dev; never permitted in production.
        return ExpectedClaims(
            required_modes=frozenset({"dev"}),
            allow_insecure_dev=True,
            expected_miner_hash=expected_miner_hash,
            model_allowlist=model_allowlist,
            require_gpu_evidence=require_gpu_evidence,
            require_tdx_quote=require_tdx_quote,
        )

    nv = nvidia or NvidiaPolicy()
    it = intel or IntelPolicy()

    nv_kwargs: dict = {}
    if nv.jwks_url is not None:
        nv_kwargs["jwks_url"] = nv.jwks_url
    if nv.expected_issuer is not None:
        nv_kwargs["expected_issuer"] = nv.expected_issuer
    if nv.nvidia_root_pem is not None:
        nv_kwargs["nvidia_root_pem"] = nv.nvidia_root_pem
    if nv.nras_url is not None:
        nv_kwargs["url"] = nv.nras_url
    if nv.allowed_algs is not None:
        nv_kwargs["allowed_algs"] = nv.allowed_algs
    nvidia_backend = NvidiaNrasBackend(**nv_kwargs)

    it_kwargs: dict = {"require_collateral": it.require_collateral}
    if it.trusted_root_pem is not None:
        it_kwargs["trusted_root_pem"] = it.trusted_root_pem
    if it.pcs_base is not None:
        it_kwargs["pcs_base"] = it.pcs_base
    if it.allowed_tcb_statuses is not None:
        it_kwargs["allowed_tcb_statuses"] = it.allowed_tcb_statuses
    intel_backend = IntelPcsTdxBackend(**it_kwargs)

    return ExpectedClaims(
        required_modes=frozenset({"nvidia"}),
        allow_insecure_dev=False,
        expected_miner_hash=expected_miner_hash,
        model_allowlist=model_allowlist,
        nvidia_backend=nvidia_backend,
        tdx_backend=intel_backend,
        require_gpu_evidence=require_gpu_evidence,
        require_tdx_quote=require_tdx_quote,
    )
