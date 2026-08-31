"""LIVE Intel PCS v4 (TDX) collateral transport + adapter tests.

These tests exercise the REAL default PCS transport (``_httpx_pcs_transport``) against the
LIVE Intel Provisioning Certification Service:

  * fetch the real ``qe/identity`` (enclaveIdentity) collateral, run it through
    ``_verify_collateral_response`` against the BUNDLED real Intel SGX Root CA, and assert
    it VERIFIES — proving the real adapter, the signature-over-raw-bytes extraction, and the
    issuer-chain header decode all work end-to-end against genuine Intel-signed collateral;
  * fetch the real ``tcb?fmspc=<public FMSPC>`` (tcbInfo) collateral likewise and assert it
    VERIFIES;
  * a NEGATIVE control: tamper the fetched signature -> verification must fail closed.

Plus an OFFLINE control (always runs): the bundled Intel SGX Root CA must match the pinned
SHA-256(DER) fingerprint ``INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256``.

The live tests are marked ``@pytest.mark.live`` and SKIP cleanly when the Intel PCS host is
unreachable (offline CI), so they never fail spuriously without network.
"""

from __future__ import annotations

import socket

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding

from talaris_attest.backends.intel_pcs import (
    DEFAULT_PCS_BASE,
    INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256,
    INTEL_SGX_ROOT_CA_PEM,
    _extract_signed_object_raw,
    _httpx_pcs_transport,
    _now,
    _verify_collateral_response,
)

# A known, public Intel FMSPC with published TDX TCB info (no subscription key needed).
PUBLIC_FMSPC = "00806f050000"

_PCS_HOST = "api.trustedservices.intel.com"


def _pcs_reachable(host: str = _PCS_HOST, port: int = 443, timeout: float = 5.0) -> bool:
    """Quick TCP reachability probe so offline CI skips the live tests cleanly."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


requires_pcs = pytest.mark.skipif(
    not _pcs_reachable(), reason="Intel PCS unreachable (offline) — skipping live tests"
)


def _bundled_root() -> x509.Certificate:
    return x509.load_pem_x509_certificate(INTEL_SGX_ROOT_CA_PEM.encode("ascii"))


# --------------------------------------------------------------------------------- #
# OFFLINE control — always runs
# --------------------------------------------------------------------------------- #

def test_bundled_root_matches_pinned_fingerprint():
    """The bundled Intel SGX Root CA must match the pinned SHA-256(DER) fingerprint. This
    is the drift guard; the module also enforces it at import time."""
    root = _bundled_root()
    h = hashes.Hash(hashes.SHA256())
    h.update(root.public_bytes(Encoding.DER))
    actual = h.finalize().hex()
    assert actual == INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256
    assert root.subject.rfc4514_string().endswith("CN=Intel SGX Root CA")


def test_brace_matcher_handles_strings_with_braces():
    """The raw-substring extractor must not be fooled by braces inside JSON string values."""
    text = '{"enclaveIdentity":{"note":"a } b { c","x":1},"signature":"ab"}'
    raw = _extract_signed_object_raw(text, "enclaveIdentity")
    assert raw == '{"note":"a } b { c","x":1}'


# --------------------------------------------------------------------------------- #
# LIVE — real Intel-signed collateral, real chain to the bundled real root
# --------------------------------------------------------------------------------- #

@pytest.mark.live
@requires_pcs
def test_live_qe_identity_verifies_to_bundled_real_root():
    resp = _httpx_pcs_transport(f"{DEFAULT_PCS_BASE}/qe/identity")
    # The real adapter must return our internal collateral shape.
    assert set(resp) == {
        "enclaveIdentity",
        "enclaveIdentity_raw",
        "signature",
        "signing_cert_chain_pem",
    }
    assert isinstance(resp["enclaveIdentity"], dict)
    assert isinstance(resp["enclaveIdentity_raw"], str) and resp["enclaveIdentity_raw"]
    # The decoded issuer chain ends at the Intel SGX Root CA (URL-decoded PEM).
    assert "BEGIN CERTIFICATE" in resp["signing_cert_chain_pem"]
    assert "Intel SGX Root CA" not in resp["signing_cert_chain_pem"]  # CN is DER, not text

    ok, reason = _verify_collateral_response(
        resp, "enclaveIdentity", _bundled_root(), _now()
    )
    assert ok is True, reason
    assert "verified" in reason


@pytest.mark.live
@requires_pcs
def test_live_tcb_info_verifies_to_bundled_real_root():
    resp = _httpx_pcs_transport(f"{DEFAULT_PCS_BASE}/tcb?fmspc={PUBLIC_FMSPC}")
    assert set(resp) == {
        "tcbInfo",
        "tcbInfo_raw",
        "signature",
        "signing_cert_chain_pem",
    }
    assert isinstance(resp["tcbInfo"], dict)
    assert resp["tcbInfo"].get("fmspc", "").lower() == PUBLIC_FMSPC
    assert isinstance(resp["tcbInfo_raw"], str) and resp["tcbInfo_raw"]

    ok, reason = _verify_collateral_response(resp, "tcbInfo", _bundled_root(), _now())
    assert ok is True, reason
    assert "verified" in reason


@pytest.mark.live
@requires_pcs
def test_live_raw_is_exact_wire_substring_not_reserialization():
    """The signed bytes are the EXACT wire substring; a json re-serialization would NOT
    match (this is the make-or-break detail of the adapter)."""
    import json

    resp = _httpx_pcs_transport(f"{DEFAULT_PCS_BASE}/tcb?fmspc={PUBLIC_FMSPC}")
    raw = resp["tcbInfo_raw"]
    reserialized = json.dumps(resp["tcbInfo"], separators=(",", ":"), sort_keys=True)
    # The raw wire bytes parse back to the same object...
    assert json.loads(raw) == resp["tcbInfo"]
    # ...but are NOT byte-identical to a canonical re-serialization (key order/spacing).
    assert raw != reserialized
    # And verifying against the raw wire bytes succeeds.
    ok, reason = _verify_collateral_response(resp, "tcbInfo", _bundled_root(), _now())
    assert ok is True, reason


@pytest.mark.live
@requires_pcs
def test_live_tampered_signature_fails_closed():
    """Flip a byte of the fetched signature -> _verify_collateral_response must reject it."""
    resp = _httpx_pcs_transport(f"{DEFAULT_PCS_BASE}/qe/identity")
    sig = bytearray(bytes.fromhex(resp["signature"]))
    sig[0] ^= 0xFF
    resp["signature"] = sig.hex()
    ok, reason = _verify_collateral_response(
        resp, "enclaveIdentity", _bundled_root(), _now()
    )
    assert ok is False
    assert "signature does not verify" in reason
