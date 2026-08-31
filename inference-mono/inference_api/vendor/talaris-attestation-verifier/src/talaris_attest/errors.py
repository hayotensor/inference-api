"""Exception hierarchy for the Talaris attestation verifier.

These are raised for *structural* problems (a malformed document, an unsupported
report_data version, a backend that cannot be constructed). Per-check verification
*failures* are NOT exceptions — they are recorded as failing CheckResult entries so the
caller gets a complete report instead of an early abort. See verify.verify_attestation.
"""

from __future__ import annotations


class AttestationError(Exception):
    """Base class for all verifier errors."""


class MalformedDocument(AttestationError):
    """The attestation document is missing required fields or has the wrong shape."""


class UnsupportedReportDataVersion(AttestationError):
    """The document advertises a report_data_version this verifier cannot recompute."""


class BackendError(AttestationError):
    """A pluggable evidence backend could not be constructed or invoked."""


class InsecureBackendInProduction(BackendError):
    """A dev/stub backend was selected while the verifier is in production mode.

    This is the SECOND of two independent dev gates (the first is the
    required_modes / allow_insecure_dev mode gate). A stub backend never validates
    hardware chains, so it must never be reachable in production.
    """
