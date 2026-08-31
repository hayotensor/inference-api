"""Dev/stub backends — NEVER validate hardware chains.

These exist so the DEV-mode attestation (where the only "evidence" is the enclave's own
Ed25519 self-signature over report_data) can be exercised end-to-end. They are
INSECURE by construction: ``is_secure = False``. verify.py refuses them in production via
the ``backend_insecure_gate`` check (the second of two independent dev gates).

  StubGpuBackend.verify(gpu_evidence, expected_nonce_hex):
      Accepts the enclave dev self-signature: verifies the document's ``dev_signature``
      (Ed25519 over report_data bytes) against the enclave verify key. It does NOT verify
      any NVIDIA signing chain. The enclave verify key and dev_signature are supplied by
      the orchestrator via a small wrapper (see DevGpuEvidence).

  StubTdxBackend.verify(quote_hex, expected_report_data):
      Accepts when a dev/absent quote is present (the producer emits tdx_quote: null in
      dev mode). Performs NO chain verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .base import BackendResult


@dataclass
class DevGpuEvidence:
    """The minimal material the stub GPU backend needs to accept a dev document.

    The orchestrator constructs this from the document: the enclave verify key, the
    dev_signature (hex), and the report_data bytes that signature is over. Carrying the
    raw gpu_evidence too lets the backend surface it for diagnostics.
    """

    verify_key_bytes: bytes
    dev_signature_hex: Optional[str]
    report_data: bytes
    gpu_uuids: tuple[str, ...] = ()
    raw_evidence: object = None


class StubGpuBackend:
    """INSECURE dev GPU backend: accepts the enclave dev self-signature only."""

    is_secure = False

    def verify(self, gpu_evidence: object, expected_nonce_hex: str) -> BackendResult:
        if not isinstance(gpu_evidence, DevGpuEvidence):
            return BackendResult(
                verified=False,
                reason=(
                    "StubGpuBackend requires a DevGpuEvidence (dev self-signature); "
                    "got real/unknown evidence — refusing"
                ),
            )
        ev = gpu_evidence
        if not ev.dev_signature_hex:
            return BackendResult(
                verified=False, reason="dev document has no dev_signature"
            )
        # The dev_signature must sign exactly the report_data the orchestrator expects,
        # and the expected nonce (report_data hex) must match that report_data.
        if ev.report_data.hex() != expected_nonce_hex:
            return BackendResult(
                verified=False,
                reason="dev evidence report_data does not match expected nonce",
            )
        try:
            signature = bytes.fromhex(ev.dev_signature_hex)
        except ValueError:
            return BackendResult(verified=False, reason="dev_signature is not valid hex")
        try:
            VerifyKey(ev.verify_key_bytes).verify(ev.report_data, signature)
        except (BadSignatureError, ValueError):
            return BackendResult(
                verified=False,
                reason="dev_signature invalid under enclave verify key",
            )
        return BackendResult(
            verified=True,
            measured_gpu_uuids=tuple(ev.gpu_uuids),
            reason="DEV self-signature accepted (NOT hardware attested)",
        )


class StubTdxBackend:
    """INSECURE dev TDX backend: accepts a dev/absent quote with no chain check."""

    is_secure = False

    def verify(self, quote_hex: object, expected_report_data: bytes) -> BackendResult:
        # Producer emits tdx_quote: null in dev mode. Accept None / absent.
        if quote_hex is None:
            return BackendResult(
                verified=True,
                reason="DEV/absent TDX quote accepted (NOT hardware attested)",
            )
        if isinstance(quote_hex, dict) and quote_hex.get("quote") is None:
            return BackendResult(
                verified=True,
                reason="DEV/absent TDX quote accepted (NOT hardware attested)",
            )
        # A present quote in dev mode is unusual; accept but flag it clearly.
        return BackendResult(
            verified=True,
            reason="DEV mode: TDX quote present but NOT chain-verified (stub backend)",
        )
