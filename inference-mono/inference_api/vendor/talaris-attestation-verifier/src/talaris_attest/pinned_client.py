"""Cert-pinned httpx clients.

After a successful attestation verification, ``verified.tls_cert_fingerprint`` is the
sha256(DER) of the EXACT self-signed leaf cert the enclave attested. A normal TLS trust
chain is useless here (the cert is self-signed inside the enclave), so we:

  * disable CA verification and hostname checking (the cert is self-signed / CN=tee-wrapper),
    and instead
  * pin the leaf: after the handshake, read the peer's DER cert and require
    sha256(peercert DER) == verified.tls_cert_fingerprint, closing the connection on
    mismatch.

This means the only cert accepted is the attested one — equivalent in trust to the
fingerprint pin, without relying on a PKI. The enclave's TLS-SPKI signature (verified in
verify_attestation's tls_pin step) separately proves the attested key vouched for this cert.
"""

from __future__ import annotations

import hashlib
import ssl
from typing import Optional

import httpx

from .result import VerificationResult


class CertFingerprintMismatch(httpx.HTTPError):
    """Raised when the served leaf cert does not match the pinned fingerprint."""


def _build_unverified_context() -> ssl.SSLContext:
    """SSL context that completes the handshake without CA/hostname verification.

    The leaf is pinned separately (post-handshake fingerprint check), so we deliberately
    do NOT verify the chain or hostname — the self-signed enclave cert has neither a CA
    nor a matching hostname in the usual sense.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _normalize_fpr(fingerprint: str) -> str:
    return fingerprint.strip().lower().replace(":", "")


class _PinningTransportMixin:
    """Shared post-handshake leaf-cert fingerprint check for sync/async transports."""

    _expected_fpr: str

    @staticmethod
    def _peer_der(stream) -> Optional[bytes]:
        # httpx exposes the negotiated ssl object via the network stream's extra info.
        ssl_obj = stream.get_extra_info("ssl_object")
        if ssl_obj is None:
            return None
        # ssl_object may be a high-level ssl.SSLSocket/SSLObject (getpeercert accepts the
        # binary_form keyword) or a raw _ssl._SSLSocket (positional only). Call positionally
        # so both work.
        return ssl_obj.getpeercert(True)

    def _assert_pin(self, der: Optional[bytes]) -> None:
        if der is None:
            raise CertFingerprintMismatch(
                "no peer certificate available to pin (was the connection TLS?)"
            )
        actual = hashlib.sha256(der).hexdigest()
        if actual != self._expected_fpr:
            raise CertFingerprintMismatch(
                f"served leaf cert fingerprint {actual} != pinned {self._expected_fpr}"
            )


class _PinningHTTPTransport(httpx.HTTPTransport, _PinningTransportMixin):
    def __init__(self, expected_fpr: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._expected_fpr = expected_fpr

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        stream = response.extensions.get("network_stream")
        if stream is not None:
            self._assert_pin(self._peer_der(stream))
        return response


class _PinningAsyncHTTPTransport(httpx.AsyncHTTPTransport, _PinningTransportMixin):
    def __init__(self, expected_fpr: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._expected_fpr = expected_fpr

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await super().handle_async_request(request)
        stream = response.extensions.get("network_stream")
        if stream is not None:
            self._assert_pin(self._peer_der(stream))
        return response


def _require_fingerprint(verified: VerificationResult) -> str:
    fpr = verified.tls_cert_fingerprint
    if not fpr:
        raise ValueError(
            "verified result has no tls_cert_fingerprint to pin against"
        )
    if not verified.ok:
        raise ValueError(
            "refusing to build a pinned client from a verification that did not pass "
            f"({len(verified.failures)} required check(s) failed)"
        )
    return _normalize_fpr(fpr)


def _headers(bearer: Optional[str]) -> dict:
    return {"Authorization": f"Bearer {bearer}"} if bearer else {}


def build_pinned_client(
    verified: VerificationResult,
    base_url: str,
    *,
    bearer: Optional[str] = None,
    **client_kwargs,
) -> httpx.Client:
    """Build a sync httpx.Client that pins the enclave's attested leaf cert.

    The client disables CA/hostname verification (self-signed enclave cert) and instead
    enforces, post-handshake, that sha256(served leaf DER) == verified.tls_cert_fingerprint.
    """
    fpr = _require_fingerprint(verified)
    ctx = _build_unverified_context()
    transport = _PinningHTTPTransport(fpr, verify=ctx)
    return httpx.Client(
        base_url=base_url,
        transport=transport,
        headers=_headers(bearer),
        **client_kwargs,
    )


def build_pinned_async_client(
    verified: VerificationResult,
    base_url: str,
    *,
    bearer: Optional[str] = None,
    **client_kwargs,
) -> httpx.AsyncClient:
    """Async variant of build_pinned_client."""
    fpr = _require_fingerprint(verified)
    ctx = _build_unverified_context()
    transport = _PinningAsyncHTTPTransport(fpr, verify=ctx)
    return httpx.AsyncClient(
        base_url=base_url,
        transport=transport,
        headers=_headers(bearer),
        **client_kwargs,
    )
