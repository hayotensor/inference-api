"""Pluggable attestation-evidence backends.

  base        — GpuEvidenceBackend / TdxQuoteBackend protocols + BackendResult.
  stub        — INSECURE dev backends (accept the enclave dev self-signature); refused in
                production by verify.py's backend_insecure_gate.
  nvidia_nras — NVIDIA NRAS REST/EAT backend (real nonce binding; signature TODO).
  nvidia_local— skeleton over nv_attestation_sdk ([nvidia-local] extra).
  intel_pcs   — TDX quote parser + report_data echo (real) + PCS/DCAP chain (skeleton).
"""

from __future__ import annotations

from .base import BackendResult, GpuEvidenceBackend, TdxQuoteBackend
from .intel_pcs import IntelPcsTdxBackend
from .nvidia_local import NvidiaLocalBackend
from .nvidia_nras import NvidiaNrasBackend
from .stub import DevGpuEvidence, StubGpuBackend, StubTdxBackend

__all__ = [
    "BackendResult",
    "GpuEvidenceBackend",
    "TdxQuoteBackend",
    "IntelPcsTdxBackend",
    "NvidiaNrasBackend",
    "NvidiaLocalBackend",
    "StubGpuBackend",
    "StubTdxBackend",
    "DevGpuEvidence",
]
