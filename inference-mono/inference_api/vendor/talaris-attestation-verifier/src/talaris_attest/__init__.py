"""talaris_attest — the shared, security-critical attestation VERIFIER for Talaris.

This is the counterpart to the TEE container's attestation PRODUCER
(``tee_wrapper.security.attestation``), which is the single source of truth. The verifier
re-implements the producer's report_data digest and TLS-SPKI binding byte-for-byte so a
relying party can independently verify an attestation document WITHOUT importing the
producer (keeping dependencies acyclic).

Public API:
    verify_attestation(doc, expected, nonce) -> VerificationResult
    ExpectedClaims, ModelAllowlistEntry, FreshnessPolicy
    VerificationResult, CheckResult
    build_pinned_client / build_pinned_async_client
    SCHEMA_VERSION
"""

from __future__ import annotations

from .claims import (
    ExpectedClaims,
    FreshnessPolicy,
    ModelAllowlistEntry,
    TdxMeasurementEntry,
    load_measurement_allowlist,
)
from .pinned_client import (
    CertFingerprintMismatch,
    build_pinned_async_client,
    build_pinned_client,
)
from .policy import IntelPolicy, NvidiaPolicy, build_expected_claims
from .result import CheckResult, VerificationResult
from .verify import verify_attestation
from .version import SCHEMA_VERSION

__all__ = [
    "verify_attestation",
    "ExpectedClaims",
    "ModelAllowlistEntry",
    "TdxMeasurementEntry",
    "load_measurement_allowlist",
    "FreshnessPolicy",
    "VerificationResult",
    "CheckResult",
    "build_pinned_client",
    "build_pinned_async_client",
    "CertFingerprintMismatch",
    "build_expected_claims",
    "NvidiaPolicy",
    "IntelPolicy",
    "SCHEMA_VERSION",
]

__version__ = "0.1.0"
