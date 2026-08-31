"""Intel TDX quote backend: REAL quote parsing + report_data echo + REAL DCAP chain.

What is REAL here (and unit-tested with structurally-real synthetic quotes):
  * parse_tdx_quote_measurements(): a byte-exact mirror of
    ``tee_wrapper.security.tdx.parse_tdx_quote_measurements`` — same v4/v5 header length,
    same TD body offsets (mrtd@136, rtmr0..3@328.., report_data@520 within the 584-byte
    body). It extracts the embedded 64-byte report_data.
  * the report_data echo check: the quote's report_data[:64] must START WITH the enclave
    report_data (the 32-byte digest, left-justified and zero-padded to 64). This is the
    binding that turns a stolen outblob into "must forge a quote that commits THIS nonce".
  * _verify_pcs_chain(): the full Intel DCAP / ECDSA-P256 (att_key_type=2) quote signature
    chain, in pure ``cryptography`` (no native DCAP libs):
      1. Parse the v4 ECDSA-256 signature data (bounds-checked, never raises).
      2. Verify the quote signature over header||td_body with the attestation key.
      3. Verify the QE report signature over the 384-byte QE report with the PCK LEAF key.
      4. Bind the attestation key to the QE: qe_report.report_data[:32] ==
         SHA256(att_key || qe_auth_data).
      5. Verify the PCK cert chain (leaf<-intermediate<-root), validity periods, and that
         the root matches the PINNED Intel SGX Root CA (or an injected test root).
      6. Extract FMSPC + TCB components from the PCK leaf SGX extension, fetch & verify the
         Intel-signed TCB info + QE identity collateral via an injectable PCS transport,
         and evaluate TCB status against policy.

The Intel PCS wire format is now REAL: ``_httpx_pcs_transport`` hits the live Intel PCS v4
(TDX) endpoints (``{base}/tcb?fmspc=...`` and ``{base}/qe/identity``), slices the EXACT raw
sub-string of the signed ``tcbInfo`` / ``enclaveIdentity`` object from the response body
(the bytes Intel signs — re-serializing would break the signature), URL-decodes the
``*-Issuer-Chain`` response header, and returns the internal collateral shape the verifier
consumes. It is validated against the live Intel PCS in ``tests/test_intel_pcs_live.py``.
The bundled Intel SGX Root CA is pinned by SHA-256(DER) fingerprint
(``INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256``) and checked at import time.

What retains a documented TODO (NOT the security-critical crypto):
  * CRL-based revocation of the PCK certs (Intel PCK CRL). The TCB-status path already
    surfaces ``Revoked`` from TCB info; PCK-CRL fetching is a future hardening step,
    marked ``TODO [intel-pcs-crl]``.

Production default is ``IntelPcsTdxBackend()`` == ``require_chain=True,
require_collateral=True`` with the bundled real Intel SGX Root CA pinned: it fails closed
on anything that does not verify end-to-end. ``require_chain=False`` (echo-only) and
``require_collateral=False`` (chain-only) are TEST/STAGING escapes, documented as NOT
production-safe.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import Callable, Optional

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509.oid import ObjectIdentifier

from ..backends.base import BackendResult, TdxQuoteBackend  # noqa: F401  (Protocol ref)

# --- TD Quote v4/v5 layout (mirror of tee_wrapper.security.tdx) --------------------
QUOTE_V4_HEADER_LEN = 48
TD_REPORT_BODY_LEN = 584

_BODY_MRTD_OFF = 136
_BODY_RTMR0_OFF = 328
_BODY_REPORT_DATA_OFF = 520

_MEASUREMENT_LEN = 48
_REPORT_DATA_LEN = 64

_MIN_QUOTE_LEN = QUOTE_V4_HEADER_LEN + TD_REPORT_BODY_LEN

TDX_QUOTE_FORMAT = "tdx_quote_v4"

# The bytes the attestation key signs: header(48) || td_report_body(584).
_SIGNED_PAYLOAD_LEN = QUOTE_V4_HEADER_LEN + TD_REPORT_BODY_LEN  # 632

# --- SGX Report (the 384-byte Quoting-Enclave report inside the sig data) ----------
# Intel SGX "Report" body. We only need report_data, which is the last 64 bytes.
SGX_REPORT_LEN = 384
_SGX_REPORT_DATA_OFF = SGX_REPORT_LEN - 64  # 320; report_data is 64 bytes at the end.
_SGX_REPORT_DATA_LEN = 64

# --- ECDSA-P256 sizes --------------------------------------------------------------
_ECDSA_SIG_LEN = 64        # r(32) || s(32)
_EC_PUBKEY_RAW_LEN = 64    # X(32) || Y(32) (uncompressed, sans the 0x04 prefix)
_P256_SCALAR_LEN = 32

# Quote cert-data type 5 == PCK certificate chain as concatenated PEM.
CERT_KEY_TYPE_PCK_CHAIN = 5

# --- Intel SGX PCK SGX-extension OIDs (Intel SGX PCK Certificate Spec) --------------
# Root extension carrying the per-platform SGX values. See:
#   Intel SGX PCK Certificate and CRL Specification, "SGX Extensions" (OID 1.2.840.113741.1.13.1)
_OID_SGX_EXTENSIONS = ObjectIdentifier("1.2.840.113741.1.13.1")
_OID_SGX_TCB = ObjectIdentifier("1.2.840.113741.1.13.1.2")
_OID_SGX_PCEID = ObjectIdentifier("1.2.840.113741.1.13.1.3")
_OID_SGX_FMSPC = ObjectIdentifier("1.2.840.113741.1.13.1.4")
# Within the TCB SEQUENCE: comp svn 1..16 then pcesvn(17) then cpusvn(18).
_OID_SGX_TCB_COMP_PREFIX = "1.2.840.113741.1.13.1.2."
_OID_SGX_TCB_PCESVN = ObjectIdentifier("1.2.840.113741.1.13.1.2.17")
_OID_SGX_TCB_CPUSVN = ObjectIdentifier("1.2.840.113741.1.13.1.2.18")

# Default TCB statuses we treat as acceptable. UpToDate is clean; SWHardeningNeeded means
# the platform is patched but a SW mitigation is recommended (commonly accepted). Anything
# else (ConfigurationNeeded / OutOfDate / Revoked / OutOfDateConfigurationNeeded ...) is
# rejected by default.
DEFAULT_ALLOWED_TCB_STATUSES = frozenset({"UpToDate", "SWHardeningNeeded"})

# Real Intel SGX Root CA, pinned. Fetched from Intel's certification service:
#   https://certificates.trustedservices.intel.com/Intel_SGX_Provisioning_Certification_RootCA.pem
# (CN="Intel SGX Root CA", self-signed EC P-256, valid 2018-05-21 .. 2049-12-31). This is
# the trust anchor for the PCK cert chain and the TCB/QE-identity signing chain. Verified
# self-signed at load time below.
INTEL_SGX_ROOT_CA_PEM = """-----BEGIN CERTIFICATE-----
MIICjzCCAjSgAwIBAgIUImUM1lqdNInzg7SVUr9QGzknBqwwCgYIKoZIzj0EAwIw
aDEaMBgGA1UEAwwRSW50ZWwgU0dYIFJvb3QgQ0ExGjAYBgNVBAoMEUludGVsIENv
cnBvcmF0aW9uMRQwEgYDVQQHDAtTYW50YSBDbGFyYTELMAkGA1UECAwCQ0ExCzAJ
BgNVBAYTAlVTMB4XDTE4MDUyMTEwNDUxMFoXDTQ5MTIzMTIzNTk1OVowaDEaMBgG
A1UEAwwRSW50ZWwgU0dYIFJvb3QgQ0ExGjAYBgNVBAoMEUludGVsIENvcnBvcmF0
aW9uMRQwEgYDVQQHDAtTYW50YSBDbGFyYTELMAkGA1UECAwCQ0ExCzAJBgNVBAYT
AlVTMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEC6nEwMDIYZOj/iPWsCzaEKi7
1OiOSLRFhWGjbnBVJfVnkY4u3IjkDYYL0MxO4mqsyYjlBalTVYxFP2sJBK5zlKOB
uzCBuDAfBgNVHSMEGDAWgBQiZQzWWp00ifODtJVSv1AbOScGrDBSBgNVHR8ESzBJ
MEegRaBDhkFodHRwczovL2NlcnRpZmljYXRlcy50cnVzdGVkc2VydmljZXMuaW50
ZWwuY29tL0ludGVsU0dYUm9vdENBLmRlcjAdBgNVHQ4EFgQUImUM1lqdNInzg7SV
Ur9QGzknBqwwDgYDVR0PAQH/BAQDAgEGMBIGA1UdEwEB/wQIMAYBAf8CAQEwCgYI
KoZIzj0EAwIDSQAwRgIhAOW/5QkR+S9CiSDcNoowLuPRLsWGf/Yi7GSX94BgwTwg
AiEA4J0lrHoMs+Xo5o/sX6O9QWxHRAvZUGOdRQ7cvqRXaqI=
-----END CERTIFICATE-----
"""

# Pinned SHA-256 fingerprint of the DER encoding of INTEL_SGX_ROOT_CA_PEM above. This is a
# drift guard: if the bundled root ever changes (typo, accidental swap, supply-chain edit),
# loading this module fails loudly instead of silently trusting a different anchor. The
# value is the SHA-256 of the cert's DER bytes (not the SPKI). Verified against the live
# Intel certification service.
INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256 = (
    "44a0196b2b99f889b8e149e95b807a350e7424964399e885a7cbb8ccfab674d3"
)


def _assert_pinned_root_fingerprint() -> None:
    """Fail closed at import time if the bundled Intel SGX Root CA no longer matches the
    pinned SHA-256(DER) fingerprint. Guards against drift in INTEL_SGX_ROOT_CA_PEM."""
    from cryptography.hazmat.primitives.serialization import Encoding

    root = x509.load_pem_x509_certificate(INTEL_SGX_ROOT_CA_PEM.encode("ascii"))
    digest = hashes.Hash(hashes.SHA256())
    digest.update(root.public_bytes(Encoding.DER))
    actual = digest.finalize().hex()
    if actual != INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256:
        raise RuntimeError(
            "bundled INTEL_SGX_ROOT_CA_PEM SHA-256(DER) fingerprint mismatch: "
            f"expected {INTEL_SGX_ROOT_CA_FINGERPRINT_SHA256}, got {actual}"
        )


# Enforce the pin as soon as the module is imported.
_assert_pinned_root_fingerprint()

# Default Intel PCS base. These are the REAL Intel PCS v4 (TDX) endpoints:
#   GET {base}/tcb?fmspc=<FMSPC_HEX>  -> {"tcbInfo": {...}, "signature": "<hex r||s>"}
#                                        + header "TCB-Info-Issuer-Chain" (URL-encoded PEM)
#   GET {base}/qe/identity            -> {"enclaveIdentity": {...}, "signature": "<hex>"}
#                                        + header "SGX-Enclave-Identity-Issuer-Chain"
# The transport is injectable so tests can inject a canned dict; the default does the real
# network call (see _httpx_pcs_transport). No subscription key is required for these public
# TDX collateral endpoints.
DEFAULT_PCS_BASE = "https://api.trustedservices.intel.com/tdx/certification/v4"

# A PCS transport is callable as transport(url) -> dict (the parsed JSON response). The
# default wraps an httpx GET; tests inject a canned dict.
PcsTransport = Callable[[str], dict]


def parse_tdx_quote_measurements(quote: bytes) -> Optional[dict]:
    """Best-effort extraction of TD measurements from a v4/v5 TD Quote.

    Byte-exact mirror of the producer's parser. Returns a dict with hex mrtd / rtmr0..3 /
    report_data on success, or None if the quote is too short or an unexpected version.
    Never raises.
    """
    if not isinstance(quote, (bytes, bytearray)):
        return None
    if len(quote) < _MIN_QUOTE_LEN:
        return None

    # Quote version: little-endian u16 at offset 0. v4 and v5 share the TD body field
    # offsets (v5 only appends fields AFTER the body). Anything else => None.
    version = int.from_bytes(quote[0:2], "little")
    if version not in (4, 5):
        return None

    body = quote[QUOTE_V4_HEADER_LEN:QUOTE_V4_HEADER_LEN + TD_REPORT_BODY_LEN]
    if len(body) < TD_REPORT_BODY_LEN:
        return None

    def _slice(off: int, length: int) -> bytes:
        return body[off:off + length]

    mrtd = _slice(_BODY_MRTD_OFF, _MEASUREMENT_LEN)
    rtmrs = [
        _slice(_BODY_RTMR0_OFF + i * _MEASUREMENT_LEN, _MEASUREMENT_LEN)
        for i in range(4)
    ]
    report_data = _slice(_BODY_REPORT_DATA_OFF, _REPORT_DATA_LEN)

    if len(mrtd) != _MEASUREMENT_LEN or len(report_data) != _REPORT_DATA_LEN:
        return None
    if any(len(r) != _MEASUREMENT_LEN for r in rtmrs):
        return None

    return {
        "mrtd": mrtd.hex(),
        "rtmr0": rtmrs[0].hex(),
        "rtmr1": rtmrs[1].hex(),
        "rtmr2": rtmrs[2].hex(),
        "rtmr3": rtmrs[3].hex(),
        "report_data": report_data.hex(),
    }


def report_data_echoes(quote: bytes, expected_report_data: bytes) -> bool:
    """True iff the quote's embedded 64-byte report_data STARTS WITH the enclave digest.

    The enclave commits a 32-byte digest, left-justified and zero-padded to 64 bytes
    (TSM inblob), so the quote's report_data[:len(expected)] must equal expected, and the
    remainder must be zero. Mirrors the producer's defense-in-depth echo check.
    """
    meas = parse_tdx_quote_measurements(quote)
    if meas is None:
        return False
    try:
        embedded = bytes.fromhex(meas["report_data"])
    except (KeyError, TypeError, ValueError):
        return False
    if len(embedded) != _REPORT_DATA_LEN:
        return False
    n = len(expected_report_data)
    if n > _REPORT_DATA_LEN:
        return False
    if embedded[:n] != expected_report_data:
        return False
    # The padding after the digest must be zero (no smuggled bytes).
    if embedded[n:] != b"\x00" * (_REPORT_DATA_LEN - n):
        return False
    return True


# --------------------------------------------------------------------------------- #
# Low-level helpers (pure, never raise on attacker-controlled input -> caught upstream)
# --------------------------------------------------------------------------------- #

class _ParseError(Exception):
    """Internal: a bounds/format failure while parsing the signature data."""


class _Reader:
    """A tiny bounds-checked cursor over a bytes buffer (little-endian primitives)."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.buf):
            raise _ParseError(
                f"truncated: need {n} bytes at offset {self.pos}, have "
                f"{len(self.buf) - self.pos}"
            )
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return out

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]


class _SignatureData:
    """Parsed ECDSA-256 quote signature data (v4 att_key_type=2)."""

    __slots__ = (
        "quote_signature", "att_key_raw", "qe_report", "qe_report_signature",
        "qe_auth_data", "cert_key_type", "cert_data",
    )

    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _parse_signature_data(quote: bytes) -> _SignatureData:
    """Parse the v4 ECDSA-256 signature data appended after header||td_body.

    Layout (Intel TDX DCAP, "ecdsa_256_quote_signature_data"):
        signature_data_len : u32  (at offset 632; the size of everything that follows)
        quote_signature    : 64   (r||s over header||td_body, SHA-256)
        ecdsa_attestation_key: 64  (raw X||Y, P-256)
        qe_report          : 384  (SGX Report of the Quoting Enclave)
        qe_report_signature: 64   (r||s over qe_report by the PCK leaf, SHA-256)
        qe_auth_data_len   : u16
        qe_auth_data       : qe_auth_data_len bytes
        cert_key_type      : u16
        cert_data_size     : u32
        cert_data          : cert_data_size bytes  (type 5 => concatenated PEM chain)

    Raises _ParseError on any truncation/format problem.
    """
    if len(quote) < _SIGNED_PAYLOAD_LEN + 4:
        raise _ParseError("quote too short to contain a signature-data length")

    r = _Reader(quote)
    r.pos = _SIGNED_PAYLOAD_LEN  # skip header(48) + td body(584) = 632
    sig_data_len = r.u32()
    sig_data = r.take(sig_data_len)  # bounded by the declared length

    sr = _Reader(sig_data)
    quote_signature = sr.take(_ECDSA_SIG_LEN)
    att_key_raw = sr.take(_EC_PUBKEY_RAW_LEN)
    qe_report = sr.take(SGX_REPORT_LEN)
    qe_report_signature = sr.take(_ECDSA_SIG_LEN)
    qe_auth_len = sr.u16()
    qe_auth_data = sr.take(qe_auth_len)
    cert_key_type = sr.u16()
    cert_data_size = sr.u32()
    cert_data = sr.take(cert_data_size)

    return _SignatureData(
        quote_signature=quote_signature,
        att_key_raw=att_key_raw,
        qe_report=qe_report,
        qe_report_signature=qe_report_signature,
        qe_auth_data=qe_auth_data,
        cert_key_type=cert_key_type,
        cert_data=cert_data,
    )


def _raw_pubkey_to_ec(raw: bytes) -> ec.EllipticCurvePublicKey:
    """Build a P-256 public key from 64 raw bytes (X||Y), via the 0x04|X|Y uncompressed
    SEC1 point. Raises ValueError/_ParseError-equivalent on a bad point."""
    if len(raw) != _EC_PUBKEY_RAW_LEN:
        raise _ParseError(f"raw EC pubkey must be {_EC_PUBKEY_RAW_LEN} bytes")
    return ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b"\x04" + raw
    )


def _rs_to_der(sig64: bytes) -> bytes:
    """Convert a 64-byte r||s ECDSA-P256 signature to DER (for cryptography's verify())."""
    if len(sig64) != _ECDSA_SIG_LEN:
        raise _ParseError(f"ECDSA signature must be {_ECDSA_SIG_LEN} bytes (r||s)")
    r = int.from_bytes(sig64[:_P256_SCALAR_LEN], "big")
    s = int.from_bytes(sig64[_P256_SCALAR_LEN:], "big")
    return encode_dss_signature(r, s)


def _verify_ecdsa(pubkey: ec.EllipticCurvePublicKey, sig64: bytes, message: bytes) -> bool:
    """Verify an ECDSA-P256/SHA-256 signature given as raw r||s over ``message``."""
    try:
        pubkey.verify(_rs_to_der(sig64), message, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, _ParseError, ValueError):
        return False


def _split_pem_chain(cert_data: bytes) -> list[x509.Certificate]:
    """Load a concatenated-PEM cert chain in document order (leaf first)."""
    text = cert_data.decode("ascii", errors="strict")
    certs: list[x509.Certificate] = []
    marker = "-----BEGIN CERTIFICATE-----"
    end = "-----END CERTIFICATE-----"
    idx = 0
    while True:
        start = text.find(marker, idx)
        if start == -1:
            break
        stop = text.find(end, start)
        if stop == -1:
            raise _ParseError("PEM chain has a BEGIN without a matching END")
        stop += len(end)
        pem = text[start:stop]
        certs.append(x509.load_pem_x509_certificate(pem.encode("ascii")))
        idx = stop
    if not certs:
        raise _ParseError("no PEM certificates in cert_data")
    return certs


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cert_time_valid(cert: x509.Certificate, now: datetime) -> bool:
    return cert.not_valid_before_utc <= now <= cert.not_valid_after_utc


def _verify_cert_signed_by(child: x509.Certificate, issuer: x509.Certificate) -> bool:
    """True iff ``child``'s signature verifies under ``issuer``'s public key (and the
    issuer/subject names line up). All certs in the DCAP chain are EC P-256/SHA-256."""
    if child.issuer != issuer.subject:
        return False
    pub = issuer.public_key()
    try:
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(
                child.signature,
                child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm),
            )
            return True
        return False
    except (InvalidSignature, ValueError, TypeError):
        return False


def _certs_equal(a: x509.Certificate, b: x509.Certificate) -> bool:
    """Identity by full DER (stronger than subject/SPKI comparison)."""
    from cryptography.hazmat.primitives.serialization import Encoding
    return a.public_bytes(Encoding.DER) == b.public_bytes(Encoding.DER)


# --- SGX extension (FMSPC + TCB) parsing -------------------------------------------

def _parse_sgx_extension(leaf: x509.Certificate) -> dict:
    """Extract FMSPC + TCB components from the PCK leaf's SGX extension.

    The extension value (OID 1.2.840.113741.1.13.1) is a DER SEQUENCE of (OID, value)
    pairs. We decode it with cryptography's ASN.1 reader if available, else a minimal
    hand-rolled DER walk. Returns {"fmspc": <hex>, "pcesvn": int, "tcb_comp": [int*16],
    "cpusvn": <hex>}. Raises _ParseError on a missing/malformed extension.
    """
    try:
        ext = leaf.extensions.get_extension_for_oid(_OID_SGX_EXTENSIONS)
    except x509.ExtensionNotFound as exc:
        raise _ParseError("PCK leaf has no SGX extension (1.2.840.113741.1.13.1)") from exc

    value = ext.value
    # x509.UnrecognizedExtension carries the raw DER bytes.
    der = getattr(value, "value", None)
    if not isinstance(der, (bytes, bytearray)):
        raise _ParseError("SGX extension value is not raw DER bytes")

    pairs = _der_sequence_of_oid_value(bytes(der))
    out: dict = {"fmspc": None, "pcesvn": None, "tcb_comp": [None] * 16, "cpusvn": None}
    for oid_str, val_tlv in pairs:
        if oid_str == _OID_SGX_FMSPC.dotted_string:
            out["fmspc"] = _der_octet_string(val_tlv).hex()
        elif oid_str == _OID_SGX_TCB.dotted_string:
            # val_tlv is a SEQUENCE of (OID, value) TCB component pairs.
            for c_oid, c_val in _der_sequence_of_oid_value(val_tlv):
                if c_oid == _OID_SGX_TCB_PCESVN.dotted_string:
                    out["pcesvn"] = _der_integer(c_val)
                elif c_oid == _OID_SGX_TCB_CPUSVN.dotted_string:
                    out["cpusvn"] = _der_octet_string(c_val).hex()
                elif c_oid.startswith(_OID_SGX_TCB_COMP_PREFIX):
                    tail = c_oid[len(_OID_SGX_TCB_COMP_PREFIX):]
                    if tail.isdigit():
                        idx = int(tail)
                        if 1 <= idx <= 16:
                            out["tcb_comp"][idx - 1] = _der_integer(c_val)
    if out["fmspc"] is None:
        raise _ParseError("SGX extension missing FMSPC (1.2.840.113741.1.13.1.4)")
    return out


# --- minimal DER walker (only what the SGX extension needs) ------------------------
#
# We avoid a heavy ASN.1 dependency: the SGX extension is small and rigidly structured.
# Each routine takes a single TLV (tag||len||value) and is bounds-checked.

def _der_read_tlv(buf: bytes, pos: int) -> tuple[int, bytes, int]:
    """Read one TLV at ``pos``; return (tag, full_tlv_bytes, next_pos)."""
    if pos + 2 > len(buf):
        raise _ParseError("DER: truncated TLV header")
    tag = buf[pos]
    length_byte = buf[pos + 1]
    hdr = 2
    if length_byte & 0x80:
        num = length_byte & 0x7F
        if num == 0 or pos + 2 + num > len(buf):
            raise _ParseError("DER: bad long-form length")
        length = int.from_bytes(buf[pos + 2:pos + 2 + num], "big")
        hdr = 2 + num
    else:
        length = length_byte
    end = pos + hdr + length
    if end > len(buf):
        raise _ParseError("DER: TLV length exceeds buffer")
    return tag, buf[pos:end], end


def _der_value(tlv: bytes) -> bytes:
    """Return the value (content) octets of a single TLV."""
    tag, full, _ = _der_read_tlv(tlv, 0)
    length_byte = tlv[1]
    if length_byte & 0x80:
        num = length_byte & 0x7F
        hdr = 2 + num
    else:
        hdr = 2
    return full[hdr:]


def _der_sequence_of_oid_value(seq_tlv: bytes) -> list[tuple[str, bytes]]:
    """Given a DER SEQUENCE TLV whose elements are 2-element SEQUENCEs (OID, value),
    return [(oid_dotted, value_tlv_bytes), ...]."""
    tag, _, _ = _der_read_tlv(seq_tlv, 0)
    if tag != 0x30:
        raise _ParseError("DER: expected SEQUENCE")
    content = _der_value(seq_tlv)
    out: list[tuple[str, bytes]] = []
    pos = 0
    while pos < len(content):
        item_tag, item_tlv, nxt = _der_read_tlv(content, pos)
        pos = nxt
        if item_tag != 0x30:
            # Skip non-SEQUENCE elements defensively.
            continue
        inner = _der_value(item_tlv)
        # First inner element: OID.
        oid_tag, oid_tlv, oid_next = _der_read_tlv(inner, 0)
        if oid_tag != 0x06:
            continue
        oid = _decode_oid(_der_value(oid_tlv))
        # Remaining bytes are the value TLV (there is exactly one).
        value_tlv = inner[oid_next:]
        out.append((oid, value_tlv))
    return out


def _decode_oid(oid_bytes: bytes) -> str:
    """Decode DER OID content octets to a dotted string."""
    if not oid_bytes:
        raise _ParseError("DER: empty OID")
    first = oid_bytes[0]
    parts = [str(first // 40), str(first % 40)]
    val = 0
    for b in oid_bytes[1:]:
        val = (val << 7) | (b & 0x7F)
        if not (b & 0x80):
            parts.append(str(val))
            val = 0
    return ".".join(parts)


def _der_integer(int_tlv: bytes) -> int:
    tag, _, _ = _der_read_tlv(int_tlv, 0)
    if tag != 0x02:
        raise _ParseError("DER: expected INTEGER")
    return int.from_bytes(_der_value(int_tlv), "big", signed=False)


def _der_octet_string(os_tlv: bytes) -> bytes:
    tag, _, _ = _der_read_tlv(os_tlv, 0)
    if tag != 0x04:
        raise _ParseError("DER: expected OCTET STRING")
    return _der_value(os_tlv)


# --------------------------------------------------------------------------------- #
# Collateral (TCB info + QE identity) verification + evaluation
# --------------------------------------------------------------------------------- #

def _verify_signed_collateral(
    payload_bytes: bytes,
    signature_hex: str,
    signing_chain: list[x509.Certificate],
    trusted_root: x509.Certificate,
    now: datetime,
) -> tuple[bool, str]:
    """Verify an Intel PCS signed-collateral blob.

    ``payload_bytes`` is the exact canonical JSON sub-object (e.g. the bytes of the
    "tcbInfo" / "enclaveIdentity" object) that Intel signs; ``signature_hex`` is the raw
    64-byte r||s ECDSA-P256 signature over it (SHA-256); ``signing_chain`` is the
    [TCB-Signing leaf, intermediate?, ...] chain that must chain to ``trusted_root``.
    """
    if not signing_chain:
        return False, "collateral has no signing certificate chain"
    # Chain the signing certs up to (and matching) the pinned root.
    ok, reason = _verify_chain_to_root(signing_chain, trusted_root, now)
    if not ok:
        return False, f"collateral signing chain invalid: {reason}"
    leaf = signing_chain[0]
    pub = leaf.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        return False, "collateral signing key is not EC"
    try:
        sig64 = bytes.fromhex(signature_hex)
    except ValueError:
        return False, "collateral signature is not valid hex"
    if not _verify_ecdsa(pub, sig64, payload_bytes):
        return False, "collateral signature does not verify"
    return True, "collateral signature verified"


def _verify_chain_to_root(
    chain: list[x509.Certificate],
    trusted_root: x509.Certificate,
    now: datetime,
) -> tuple[bool, str]:
    """Verify ``chain`` (leaf-first) links each-to-next and terminates at a cert equal to
    (or signed by) ``trusted_root``; check validity periods of every cert."""
    full = list(chain)
    # If the chain doesn't already include the root, append the pinned root so the top
    # link is checked against it.
    if not _certs_equal(full[-1], trusted_root):
        full = full + [trusted_root]
    for cert in full:
        if not _cert_time_valid(cert, now):
            return False, f"certificate '{cert.subject.rfc4514_string()}' outside validity period"
    for i in range(len(full) - 1):
        if not _verify_cert_signed_by(full[i], full[i + 1]):
            return (
                False,
                f"'{full[i].subject.rfc4514_string()}' not signed by "
                f"'{full[i + 1].subject.rfc4514_string()}'",
            )
    # The terminal cert must equal the pinned root (identity, not just name).
    if not _certs_equal(full[-1], trusted_root):
        return False, "chain does not terminate at the pinned trusted root"
    # The pinned root must be self-signed (sanity; it is by construction).
    if not _verify_cert_signed_by(trusted_root, trusted_root):
        return False, "pinned trusted root is not self-signed"
    return True, "chain verified to pinned root"


def _evaluate_tcb_status(
    tcb_info: dict,
    sgx_ext: dict,
    allowed_statuses: frozenset,
) -> tuple[bool, str]:
    """Evaluate the platform's TCB status from TCB info + the PCK leaf's TCB components.

    Intel TCB info lists ``tcbLevels`` from newest to oldest. The platform's status is the
    status of the FIRST level whose every SGX TCB component (and pcesvn) is <= the
    platform's reported component (i.e. the platform is at-or-above that level). We compare
    against the FMSPC-scoped tcbInfo.
    """
    info = tcb_info.get("tcbInfo", tcb_info)
    # FMSPC must match the PCK leaf (defense against substituting another platform's info).
    info_fmspc = str(info.get("fmspc", "")).lower()
    if info_fmspc and sgx_ext.get("fmspc") and info_fmspc != sgx_ext["fmspc"].lower():
        return False, (
            f"TCB info FMSPC {info_fmspc} != PCK leaf FMSPC {sgx_ext['fmspc'].lower()}"
        )

    plat_comp = sgx_ext.get("tcb_comp") or []
    plat_pcesvn = sgx_ext.get("pcesvn")
    levels = info.get("tcbLevels") or []
    if not levels:
        return False, "TCB info has no tcbLevels"

    for level in levels:
        tcb = level.get("tcb", {})
        # Newer TCB info nests SGX comps under "sgxtcbcomponents": [{"svn": n}, ...];
        # older flat form uses sgxtcbcompNNsvn keys. Support both.
        comps = _level_sgx_comps(tcb)
        level_pcesvn = tcb.get("pcesvn")
        if level_pcesvn is None:
            continue
        if len(comps) < 16 or len(plat_comp) < 16:
            continue
        if any(c is None for c in plat_comp[:16]):
            continue
        meets = all(plat_comp[i] >= comps[i] for i in range(16))
        if plat_pcesvn is not None:
            meets = meets and (plat_pcesvn >= level_pcesvn)
        if meets:
            status = level.get("tcbStatus", "Unknown")
            if status in allowed_statuses:
                return True, f"TCB status {status!r} is allowed"
            return False, f"TCB status {status!r} not in allowed set {sorted(allowed_statuses)}"
    return False, "platform TCB is below every level in TCB info (OutOfDate)"


def _level_sgx_comps(tcb: dict) -> list:
    """Extract the 16 SGX TCB component SVNs from a tcbLevel.tcb object (both schemas)."""
    arr = tcb.get("sgxtcbcomponents")
    if isinstance(arr, list) and arr:
        return [c.get("svn") if isinstance(c, dict) else c for c in arr]
    out = []
    for i in range(1, 17):
        out.append(tcb.get(f"sgxtcbcomp{i:02d}svn"))
    return out


# --------------------------------------------------------------------------------- #
# The main chain verification
# --------------------------------------------------------------------------------- #

def _verify_pcs_chain(
    quote: bytes,
    *,
    trusted_root: x509.Certificate,
    pcs_transport: PcsTransport,
    allowed_tcb_statuses: frozenset,
    require_collateral: bool,
    pcs_base: str = DEFAULT_PCS_BASE,
) -> tuple[bool, str]:
    """REAL Intel DCAP / ECDSA-P256 quote signature-chain verification (pure cryptography).

    Returns (True, reason) on full success, else (False, reason). NEVER raises on
    attacker-controlled input: every parse/crypto failure is converted to (False, reason).
    """
    now = _now()

    # --- step 1: parse the signature data (bounds-checked) -------------------------
    try:
        sd = _parse_signature_data(quote)
    except _ParseError as exc:
        return False, f"signature-data parse failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - never trust the quote bytes
        return False, f"signature-data parse error: {type(exc).__name__}: {exc}"

    if sd.cert_key_type != CERT_KEY_TYPE_PCK_CHAIN:
        return False, (
            f"unsupported cert_key_type {sd.cert_key_type} "
            f"(only type {CERT_KEY_TYPE_PCK_CHAIN} = PCK PEM chain supported)"
        )

    # --- step 2: verify the quote signature with the attestation key ---------------
    signed_payload = quote[:_SIGNED_PAYLOAD_LEN]
    try:
        att_key = _raw_pubkey_to_ec(sd.att_key_raw)
    except (_ParseError, ValueError) as exc:
        return False, f"attestation key is not a valid P-256 point: {exc}"
    if not _verify_ecdsa(att_key, sd.quote_signature, signed_payload):
        return False, "quote signature does not verify under the attestation key"

    # --- step 3: load the PCK cert chain, verify the QE report signature -----------
    try:
        pck_chain = _split_pem_chain(sd.cert_data)
    except (_ParseError, ValueError) as exc:
        return False, f"PCK cert chain parse failed: {exc}"
    pck_leaf = pck_chain[0]
    leaf_pub = pck_leaf.public_key()
    if not isinstance(leaf_pub, ec.EllipticCurvePublicKey):
        return False, "PCK leaf public key is not EC"
    if not _verify_ecdsa(leaf_pub, sd.qe_report_signature, sd.qe_report):
        return False, "QE report signature does not verify under the PCK leaf key"

    # --- step 4: bind the attestation key to the QE report -------------------------
    # qe_report.report_data[:32] == SHA256(ecdsa_attestation_key || qe_auth_data).
    if len(sd.qe_report) != SGX_REPORT_LEN:
        return False, "QE report has wrong length"
    qe_report_data = sd.qe_report[_SGX_REPORT_DATA_OFF:_SGX_REPORT_DATA_OFF + _SGX_REPORT_DATA_LEN]
    h = hashes.Hash(hashes.SHA256())
    h.update(sd.att_key_raw)
    h.update(sd.qe_auth_data)
    binding = h.finalize()
    if qe_report_data[:32] != binding:
        return False, (
            "attestation key is not bound to the QE report "
            "(SHA256(att_key||qe_auth_data) != qe_report.report_data[:32])"
        )

    # --- step 5: verify the PCK cert chain to the pinned root ----------------------
    ok, reason = _verify_chain_to_root(pck_chain, trusted_root, now)
    if not ok:
        return False, f"PCK chain: {reason}"

    # --- step 6: FMSPC/TCB extraction + collateral (TCB info + QE identity) ---------
    try:
        sgx_ext = _parse_sgx_extension(pck_leaf)
    except (_ParseError, ValueError) as exc:
        return False, f"PCK leaf SGX extension parse failed: {exc}"
    fmspc = sgx_ext["fmspc"]

    # Fetch collateral via the injectable transport. If unavailable and required -> fail
    # closed; if not required, skip (documented NOT production-safe).
    try:
        tcb_info_resp = pcs_transport(f"{pcs_base}/tcb?fmspc={fmspc}")
        qe_identity_resp = pcs_transport(f"{pcs_base}/qe/identity")
    except Exception as exc:  # noqa: BLE001 - transport/network failures fail closed
        if require_collateral:
            return False, f"PCS collateral fetch failed (require_collateral=True): {exc}"
        return True, (
            "PCK chain + quote/QE signatures verified; PCS collateral SKIPPED "
            "(require_collateral=False — NOT production-safe): " + str(exc)
        )

    if not isinstance(tcb_info_resp, dict) or not isinstance(qe_identity_resp, dict):
        if require_collateral:
            return False, "PCS collateral responses are not JSON objects"
        return True, (
            "PCK chain + signatures verified; PCS collateral malformed but "
            "require_collateral=False (NOT production-safe)"
        )

    # Verify the TCB info is Intel-signed and chains to the pinned root.
    ok, reason = _verify_collateral_response(
        tcb_info_resp, "tcbInfo", trusted_root, now
    )
    if not ok:
        if require_collateral:
            return False, f"TCB info collateral invalid: {reason}"
        return True, f"signatures verified; TCB info unverified (require_collateral=False): {reason}"

    ok, reason = _verify_collateral_response(
        qe_identity_resp, "enclaveIdentity", trusted_root, now
    )
    if not ok:
        if require_collateral:
            return False, f"QE identity collateral invalid: {reason}"
        return True, f"signatures verified; QE identity unverified (require_collateral=False): {reason}"

    # Evaluate the platform TCB status against policy.
    ok, reason = _evaluate_tcb_status(tcb_info_resp, sgx_ext, allowed_tcb_statuses)
    if not ok:
        return False, f"TCB evaluation: {reason}"

    return True, (
        "DCAP chain verified: quote+QE signatures, att-key binding, PCK chain to pinned "
        f"Intel SGX Root CA, Intel-signed TCB info + QE identity, {reason}"
    )


def _verify_collateral_response(
    resp: dict,
    payload_key: str,
    trusted_root: x509.Certificate,
    now: datetime,
) -> tuple[bool, str]:
    """Verify an Intel PCS signed-collateral JSON response.

    The PCS returns ``{"<payload_key>": {...}, "signature": "<hex r||s>"}`` and ships the
    signing cert chain in an HTTP header (``TCB-Info-Issuer-Chain`` /
    ``SGX-Enclave-Identity-Issuer-Chain``). Our injectable transport surfaces that header
    as ``resp["signing_cert_chain_pem"]`` (concatenated PEM). The signature is over the
    EXACT serialized JSON bytes of the payload object AS THEY APPEAR ON THE WIRE; the
    transport surfaces those bytes as ``resp["<payload_key>_raw"]`` (the verbatim sub-string
    Intel signed, sliced out of the response body by ``_extract_signed_object_raw``). The
    real ``_httpx_pcs_transport`` always supplies ``_raw``. If it is absent (e.g. a
    hand-built collateral dict) we fall back to a canonical ``json.dumps`` — that fallback
    only matches signatures computed over the canonical form, NOT Intel's wire bytes.
    """
    payload = resp.get(payload_key)
    if not isinstance(payload, dict):
        return False, f"collateral missing '{payload_key}' object"
    signature_hex = resp.get("signature")
    if not isinstance(signature_hex, str):
        return False, "collateral missing 'signature'"
    chain_pem = resp.get("signing_cert_chain_pem")
    if not isinstance(chain_pem, str) or not chain_pem.strip():
        return False, "collateral missing 'signing_cert_chain_pem'"
    try:
        signing_chain = _split_pem_chain(chain_pem.encode("ascii"))
    except (_ParseError, ValueError) as exc:
        return False, f"collateral signing chain parse failed: {exc}"

    raw = resp.get(f"{payload_key}_raw")
    if isinstance(raw, str):
        payload_bytes = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        payload_bytes = bytes(raw)
    else:
        # The real transport always supplies ``_raw`` (the exact wire sub-string Intel
        # signs). This canonical fallback only applies to hand-built collateral whose
        # signature was computed over the canonical form; it will NOT match Intel's wire
        # bytes, which are sliced verbatim by ``_extract_signed_object_raw``.
        import json
        payload_bytes = json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    return _verify_signed_collateral(
        payload_bytes, signature_hex, signing_chain, trusted_root, now
    )


# The signed sub-object key -> the issuer-chain response header that carries the URL-encoded
# PEM signing chain ([Intel SGX TCB Signing leaf, Intel SGX Root CA]). httpx headers are
# case-insensitive, so the casing here is cosmetic.
_ISSUER_CHAIN_HEADERS = {
    "tcbInfo": "TCB-Info-Issuer-Chain",
    "enclaveIdentity": "SGX-Enclave-Identity-Issuer-Chain",
}


def _extract_signed_object_raw(text: str, key: str) -> str:
    """Extract the EXACT verbatim sub-string of the ``key`` object from a PCS response body.

    Intel signs the EXACT serialized bytes of the ``tcbInfo`` / ``enclaveIdentity`` object
    as they appear in the response body — NOT a re-canonicalization. Re-serializing with
    ``json.dumps`` would reorder/respace keys and the signature would NOT verify, so we must
    slice the raw bytes out of the response text.

    This locates ``"<key>"``, skips to its value's opening ``{``, and brace-matches to the
    closing ``}`` while respecting JSON string literals (so braces inside strings don't
    throw off the depth count). Returns the verbatim substring ``text[open:close+1]``.
    Raises ValueError if the key/object is absent or the braces never balance.
    """
    needle = '"' + key + '"'
    ki = text.find(needle)
    if ki == -1:
        raise ValueError(f"signed object key {key!r} not found in PCS response body")
    ci = text.find(":", ki + len(needle))
    if ci == -1:
        raise ValueError(f"no ':' after key {key!r} in PCS response body")
    start = text.find("{", ci)
    if start == -1:
        raise ValueError(f"value of {key!r} is not a JSON object")
    depth = 0
    in_str = False
    esc = False
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    raise ValueError(f"unbalanced braces while extracting {key!r} object")


def _httpx_pcs_transport(url: str) -> dict:
    """Default production PCS transport against the REAL Intel PCS v4 (TDX) endpoints.

    GETs ``url`` (one retry on transport/5xx error), parses the JSON body to find which
    signed sub-object is present (``tcbInfo`` for /tcb, ``enclaveIdentity`` for /qe/identity),
    slices the EXACT raw substring of that object from the response *text* (the bytes Intel
    actually signed — see ``_extract_signed_object_raw``; re-serializing would break the
    signature), URL-decodes the matching ``*-Issuer-Chain`` response header (whose last cert
    is the Intel SGX Root CA), and returns the internal collateral shape that
    ``_verify_collateral_response`` consumes:

        {<kind>: <parsed obj>, "<kind>_raw": <raw substring str>,
         "signature": <hex r||s>, "signing_cert_chain_pem": <decoded PEM chain>}

    where ``<kind>`` is ``"tcbInfo"`` or ``"enclaveIdentity"``. No subscription key is
    required for these public TDX collateral endpoints.
    """
    import urllib.parse

    import httpx

    last_exc: Optional[Exception] = None
    resp = None
    for attempt in range(2):  # one retry
        try:
            resp = httpx.get(url, timeout=30.0)
            resp.raise_for_status()
            break
        except (httpx.HTTPError,) as exc:  # transport errors + non-2xx status
            last_exc = exc
            resp = None
    if resp is None:
        raise last_exc if last_exc is not None else RuntimeError(f"PCS GET failed: {url}")

    text = resp.text
    body = resp.json()

    # Pick the kind by which signed sub-object the body carries.
    if "tcbInfo" in body:
        kind = "tcbInfo"
    elif "enclaveIdentity" in body:
        kind = "enclaveIdentity"
    else:
        raise ValueError(
            f"PCS response has neither 'tcbInfo' nor 'enclaveIdentity': {url}"
        )

    raw = _extract_signed_object_raw(text, kind)

    chain_header = _ISSUER_CHAIN_HEADERS[kind]
    encoded_chain = resp.headers.get(chain_header)
    if encoded_chain is None:
        raise ValueError(
            f"PCS response missing issuer-chain header {chain_header!r} for {url}"
        )
    chain_pem = urllib.parse.unquote(encoded_chain)

    signature = body.get("signature")
    if not isinstance(signature, str):
        raise ValueError(f"PCS response missing 'signature' for {url}")

    return {
        kind: body[kind],
        f"{kind}_raw": raw,
        "signature": signature,
        "signing_cert_chain_pem": chain_pem,
    }


class IntelPcsTdxBackend:
    """Real TDX backend with REAL DCAP signature-chain verification.

    The quote PARSING, report_data ECHO, and the full ECDSA-P256 DCAP signature chain
    (quote signature, QE report signature, att-key binding, PCK chain to the pinned Intel
    SGX Root CA, and Intel-signed TCB info + QE identity collateral with a TCB-status
    policy) are all REAL and unit-tested against structurally-real synthetic quotes.

    Production default: ``IntelPcsTdxBackend()`` == ``require_chain=True,
    require_collateral=True`` with the bundled Intel SGX Root CA pinned. It fails closed on
    anything that does not verify end-to-end.

    Test/staging escapes (documented NOT production-safe):
      * ``require_chain=False`` — echo-only; skips the entire DCAP chain.
      * ``require_collateral=False`` — chain-only; verifies signatures + PCK chain but
        tolerates missing/unverified PCS collateral.
      * ``trusted_root_pem=<test root>`` and ``pcs_transport=<canned>`` let tests drive the
        real logic with a synthetic root + injected collateral, no network/hardware.
    """

    is_secure = True

    def __init__(
        self,
        *,
        require_chain: bool = True,
        trusted_root_pem: Optional[str] = None,
        pcs_transport: Optional[PcsTransport] = None,
        allowed_tcb_statuses: frozenset = DEFAULT_ALLOWED_TCB_STATUSES,
        require_collateral: bool = True,
        pcs_base: str = DEFAULT_PCS_BASE,
    ) -> None:
        self.require_chain = require_chain
        self.require_collateral = require_collateral
        self.allowed_tcb_statuses = frozenset(allowed_tcb_statuses)
        self.pcs_base = pcs_base
        self._pcs_transport = pcs_transport or _httpx_pcs_transport
        # Pin the bundled real Intel SGX Root CA unless a (test) root is injected.
        pem = trusted_root_pem if trusted_root_pem is not None else INTEL_SGX_ROOT_CA_PEM
        self._trusted_root = x509.load_pem_x509_certificate(pem.encode("ascii"))

    def verify(self, quote_hex: object, expected_report_data: bytes) -> BackendResult:
        # Accept either a hex string or a producer-style tdx_quote dict {"quote": <hex>}.
        raw_hex = quote_hex
        if isinstance(quote_hex, dict):
            raw_hex = quote_hex.get("quote")
        if not isinstance(raw_hex, str):
            return BackendResult(
                verified=False,
                reason="tdx quote missing or not a hex string",
            )
        try:
            quote = bytes.fromhex(raw_hex)
        except ValueError:
            return BackendResult(verified=False, reason="tdx quote is not valid hex")

        measurements = parse_tdx_quote_measurements(quote)
        if measurements is None:
            return BackendResult(
                verified=False,
                reason="could not parse TD Quote v4/v5 measurements (version/length)",
            )

        # report_data echo is enforced BEFORE the chain (cheap, binds THIS nonce).
        if not report_data_echoes(quote, expected_report_data):
            return BackendResult(
                verified=False,
                measurements=measurements,
                reason="quote report_data[:64] does not echo the enclave report_data",
            )

        # Echo passed. Now the REAL PCS/DCAP chain (unless explicitly disabled).
        if self.require_chain:
            chain_ok, chain_reason = _verify_pcs_chain(
                quote,
                trusted_root=self._trusted_root,
                pcs_transport=self._pcs_transport,
                allowed_tcb_statuses=self.allowed_tcb_statuses,
                require_collateral=self.require_collateral,
                pcs_base=self.pcs_base,
            )
            if not chain_ok:
                return BackendResult(
                    verified=False,
                    measurements=measurements,
                    reason=chain_reason,
                )
            return BackendResult(
                verified=True,
                measurements=measurements,
                reason="TDX report_data echo verified; " + chain_reason,
            )

        return BackendResult(
            verified=True,
            measurements=measurements,
            reason=(
                "TDX report_data echo verified; PCS/DCAP chain SKIPPED "
                "(require_chain=False — NOT production-safe)"
            ),
        )
