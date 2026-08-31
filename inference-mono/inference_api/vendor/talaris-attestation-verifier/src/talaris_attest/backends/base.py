"""Pluggable evidence-backend interfaces.

Two backend kinds:

  GpuEvidenceBackend.verify(gpu_evidence, expected_nonce_hex) -> BackendResult
      Verify NVIDIA-signed per-GPU evidence AND that the evidence's embedded nonce equals
      the enclave report_data hex (the digest the GPU signed over). Returns the measured
      GPU UUIDs so the orchestrator can cross-check them against doc.gpu_uuids.

  TdxQuoteBackend.verify(quote_hex, expected_report_data: bytes) -> BackendResult
      Verify the Intel TDX quote's signature chain (PCS/DCAP) AND that the quote's
      embedded report_data[:64] echoes the enclave report_data.

A backend reports `verified=True` only when its checks pass. `is_secure` distinguishes
real hardware backends from dev/stub backends; verify.py uses it for the second dev gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class BackendResult:
    """Outcome of a single backend verification."""

    verified: bool
    measured_gpu_uuids: tuple[str, ...] = ()
    measurements: Optional[dict] = None
    reason: Optional[str] = None
    detail: dict = field(default_factory=dict)


@runtime_checkable
class GpuEvidenceBackend(Protocol):
    #: Real hardware backends set this True; dev/stub backends set it False so verify.py
    #: can refuse an insecure backend in production (the second dev gate).
    is_secure: bool

    def verify(self, gpu_evidence: object, expected_nonce_hex: str) -> BackendResult:
        ...


@runtime_checkable
class TdxQuoteBackend(Protocol):
    is_secure: bool

    def verify(self, quote_hex: object, expected_report_data: bytes) -> BackendResult:
        ...
