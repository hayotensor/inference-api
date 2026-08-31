"""Intel TDX backend: REAL quote parsing + report_data echo (unit-testable).

We synthesize a TDX v4-shaped quote (48-byte header with version=4, 584-byte TD body)
with a known report_data, mrtd and rtmrs placed at the producer-mirrored offsets, and:
  * parse_tdx_quote_measurements extracts the right fields;
  * report_data_echoes is True for the matching enclave digest, False otherwise;
  * IntelPcsTdxBackend(require_chain=False) verifies via the echo (chain skipped);
  * IntelPcsTdxBackend(require_chain=True) FAILS CLOSED when the quote carries no real
    ECDSA signature data (the REAL DCAP chain parse fails). The full positive chain path
    against a fully-signed synthetic quote lives in tests/test_intel_pcs_chain.py.
  * a too-short / wrong-version quote yields no measurements.
"""

from __future__ import annotations

from talaris_attest.backends.intel_pcs import (
    IntelPcsTdxBackend,
    parse_tdx_quote_measurements,
    report_data_echoes,
)

# Offsets mirror tee_wrapper.security.tdx (relative to the 584-byte TD body).
HEADER_LEN = 48
BODY_LEN = 584
MRTD_OFF = 136
RTMR0_OFF = 328
REPORT_DATA_OFF = 520
MEAS_LEN = 48
RD_LEN = 64


def _synthetic_quote(report_data_digest: bytes, *, version: int = 4) -> bytes:
    """Build a v4-shaped TD quote: header(48) + body(584) with fields at mirrored offsets.

    report_data field is the 32-byte digest, left-justified and zero-padded to 64 bytes,
    exactly as the producer's TSM inblob writes it.
    """
    assert len(report_data_digest) <= RD_LEN
    header = bytearray(HEADER_LEN)
    header[0:2] = version.to_bytes(2, "little")  # quote version (little-endian u16)

    body = bytearray(BODY_LEN)
    body[MRTD_OFF:MRTD_OFF + MEAS_LEN] = bytes([0x11]) * MEAS_LEN
    for i in range(4):
        off = RTMR0_OFF + i * MEAS_LEN
        body[off:off + MEAS_LEN] = bytes([0x20 + i]) * MEAS_LEN
    rd_field = report_data_digest.ljust(RD_LEN, b"\x00")
    body[REPORT_DATA_OFF:REPORT_DATA_OFF + RD_LEN] = rd_field

    return bytes(header) + bytes(body)


def test_parse_extracts_fields():
    digest = bytes.fromhex("ab" * 32)
    quote = _synthetic_quote(digest)
    meas = parse_tdx_quote_measurements(quote)
    assert meas is not None
    assert meas["mrtd"] == ("11" * MEAS_LEN)
    assert meas["rtmr0"] == ("20" * MEAS_LEN)
    assert meas["rtmr3"] == ("23" * MEAS_LEN)
    # 64-byte report_data = 32-byte digest + 32 zero bytes.
    assert meas["report_data"] == digest.hex() + ("00" * 32)


def test_report_data_echo_matches_and_mismatches():
    digest = bytes.fromhex("ab" * 32)
    quote = _synthetic_quote(digest)
    assert report_data_echoes(quote, digest) is True
    # A different digest must not echo.
    assert report_data_echoes(quote, bytes.fromhex("cd" * 32)) is False
    # Non-zero padding after the digest must be rejected.
    bad = bytearray(quote)
    bad[HEADER_LEN + REPORT_DATA_OFF + 32] = 0x01  # smuggled byte in the padding
    assert report_data_echoes(bytes(bad), digest) is False


def test_v5_quote_also_parses():
    digest = bytes.fromhex("99" * 32)
    quote = _synthetic_quote(digest, version=5)
    meas = parse_tdx_quote_measurements(quote)
    assert meas is not None
    assert meas["report_data"] == digest.hex() + ("00" * 32)


def test_short_or_wrong_version_no_measurements():
    assert parse_tdx_quote_measurements(b"\x04\x00too-short") is None
    digest = bytes.fromhex("ab" * 32)
    bad_version = bytearray(_synthetic_quote(digest))
    bad_version[0:2] = (7).to_bytes(2, "little")  # unsupported version
    assert parse_tdx_quote_measurements(bytes(bad_version)) is None


def test_backend_echo_verifies_without_chain():
    digest = bytes.fromhex("ab" * 32)
    quote = _synthetic_quote(digest)
    backend = IntelPcsTdxBackend(require_chain=False)
    res = backend.verify(quote.hex(), digest)
    assert res.verified is True
    assert res.measurements is not None
    # A wrong expected report_data fails the echo.
    assert backend.verify(quote.hex(), bytes.fromhex("cd" * 32)).verified is False


def test_backend_accepts_producer_quote_dict():
    """The backend tolerates a producer-style {'quote': <hex>} tdx_quote dict."""
    digest = bytes.fromhex("ab" * 32)
    quote = _synthetic_quote(digest)
    backend = IntelPcsTdxBackend(require_chain=False)
    res = backend.verify({"quote": quote.hex(), "format": "tdx_quote_v4"}, digest)
    assert res.verified is True


def test_backend_fails_closed_when_chain_required():
    digest = bytes.fromhex("ab" * 32)
    quote = _synthetic_quote(digest)  # header+body only: NO ECDSA signature data appended
    backend = IntelPcsTdxBackend(require_chain=True)  # default, production-intended
    res = backend.verify(quote.hex(), digest)
    # Echo passes, but with require_chain=True the REAL DCAP chain runs and this quote has
    # no signature data to verify, so it fails closed at the signature-data parse step.
    assert res.verified is False
    assert "signature-data" in (res.reason or "")
    # (A structurally-real, fully-signed quote that DOES verify the chain is covered by
    # tests/test_intel_pcs_chain.py against a pinned synthetic root + injected collateral.)


def test_backend_is_secure():
    assert IntelPcsTdxBackend.is_secure is True
