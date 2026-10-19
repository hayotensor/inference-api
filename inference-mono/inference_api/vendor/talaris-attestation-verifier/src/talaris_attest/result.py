"""Result types for attestation verification.

Verification never throws on a *failed check*; instead each step appends a CheckResult.
``VerificationResult.ok`` is True iff every REQUIRED check passed. Informational checks
(``required=False``) never affect ``ok`` — they record context such as "gpu_hash absent,
engine not ready" without failing the whole verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class CheckResult:
    """The outcome of a single verification step.

    name:     stable identifier for the step (e.g. "report_data").
    passed:   whether the check succeeded.
    reason:   human-readable explanation (always set; concise on success too).
    detail:   optional structured context (measured UUIDs, mismatched hex, ...).
    required: if True, a failure makes the overall result not-ok. Informational
              checks set this False so they can record context without failing.
    """

    name: str
    passed: bool
    reason: str
    detail: Optional[dict[str, Any]] = None
    required: bool = True


@dataclass
class VerificationResult:
    """Aggregate result of verifying an attestation document."""

    checks: list[CheckResult] = field(default_factory=list)

    # Material a caller needs after a successful verify (populated as steps run).
    mode: Optional[str] = None
    tls_cert_fingerprint: Optional[str] = None
    measured_gpu_uuids: tuple[str, ...] = ()
    report_data_hex: Optional[str] = None
    # Hex MLKEM768-X25519 HPKE public key, set only once identity_binding proved it is
    # covered by the attested fingerprint. None for enclaves without E2EE.
    e2ee_key_hex: Optional[str] = None

    def add(self, check: CheckResult) -> CheckResult:
        self.checks.append(check)
        return check

    @property
    def ok(self) -> bool:
        """True iff every REQUIRED check passed (and at least one check ran)."""
        if not self.checks:
            return False
        return all(c.passed for c in self.checks if c.required)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.required and not c.passed]

    def summary(self) -> str:
        status = "OK" if self.ok else "FAIL"
        lines = [f"verification: {status}"]
        for c in self.checks:
            flag = "PASS" if c.passed else ("FAIL" if c.required else "INFO")
            lines.append(f"  [{flag}] {c.name}: {c.reason}")
        return "\n".join(lines)
