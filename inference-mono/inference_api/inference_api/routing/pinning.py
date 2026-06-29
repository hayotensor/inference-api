"""Build a TLS-leaf-pinned httpx async client for talking to an enclave.

httpx has no native leaf-pinning, so we REUSE the verifier package's
``build_pinned_async_client``, which installs a transport that, post-handshake,
compares ``sha256(served leaf DER)`` against the attested
``tls_cert_fingerprint`` and aborts on mismatch. We adapt our stored fingerprint
into the ``VerificationResult`` that helper expects (a single passing check +
the fingerprint), so the actual pin logic stays the single source of truth in
``talaris_attest``.

For tests an explicit ``transport`` may be injected (the in-process dev-TEE
TestClient transport); pinning is then bypassed because the test transport
serves no real TLS cert.
"""

from __future__ import annotations

import httpx

from talaris_attest import build_pinned_async_client
from talaris_attest.result import CheckResult, VerificationResult

from inference_api.config import settings


class PinError(RuntimeError):
    """Raised when a pinned client cannot be built (no fingerprint, etc.)."""


def _verified_for_fingerprint(fingerprint: str) -> VerificationResult:
    result = VerificationResult()
    result.tls_cert_fingerprint = fingerprint
    result.add(CheckResult("tls_pin_adapter", True, "pin fingerprint supplied for forwarding"))
    return result


def build_enclave_client(
    *,
    base_url: str,
    tls_cert_fingerprint: str | None,
    bearer: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float | httpx.Timeout | None = None,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient for the enclave, TLS-leaf-pinned when enforced.

    * ``transport`` injected (tests): used verbatim; pinning is not applied.
    * ``tls_pin_enforce`` True and a fingerprint is known: a verifier-built pinned
      client is returned.
    * otherwise: a plain client (pinning disabled / fingerprint unknown).
    """
    if timeout is None:
        timeout = httpx.Timeout(
            settings.tee_forward_timeout_seconds,
            connect=settings.tee_connect_timeout_seconds,
        )
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}

    if transport is not None:
        return httpx.AsyncClient(
            base_url=base_url, transport=transport, headers=headers, timeout=timeout
        )

    if settings.tls_pin_enforce:
        if not tls_cert_fingerprint:
            raise PinError("TLS pinning is enforced but the miner has no attested fingerprint")
        return build_pinned_async_client(
            _verified_for_fingerprint(tls_cert_fingerprint),
            base_url,
            bearer=bearer,
            timeout=timeout,
        )

    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)
