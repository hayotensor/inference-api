"""Synthetic-but-structurally-real TDX v4 quote + collateral builder (NOT collected).

This helper BUILDS a complete, cryptographically-consistent TDX v4 (ECDSA-P256,
att_key_type=2) quote plus the matching PCS collateral so the verifier's REAL DCAP chain
can be exercised offline (no Intel hardware, no network). Everything is signed correctly
with a SYNTHETIC "Intel SGX Root CA" the test pins in place of the production root.

Layers built:
  * a synthetic EC P-256 root CA ("Intel SGX Root CA"),
  * an intermediate ("Intel SGX PCK Processor CA") signed by the root,
  * a PCK LEAF (with a real SGX extension carrying a FMSPC + 16 TCB comps + pcesvn) signed
    by the intermediate,
  * a TCB-Signing leaf signed by the root, used to sign the TCB info + QE identity JSON,
  * an attestation key (P-256) and a 384-byte QE report whose report_data[:32] binds the
    att key, with the QE report signed by the PCK leaf,
  * the quote signature over header||td_body by the attestation key,
  * and the assembled quote bytes (header + 584-byte TD body + u32 len + sig data).

The byte layout MIRRORS talaris_attest.backends.intel_pcs exactly; a wrong offset here
would make the verifier reject a genuine quote, so this file is the parser's inverse.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import struct
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID, ObjectIdentifier

# Offsets/lengths MUST match talaris_attest.backends.intel_pcs.
QUOTE_V4_HEADER_LEN = 48
TD_REPORT_BODY_LEN = 584
SGX_REPORT_LEN = 384
_BODY_REPORT_DATA_OFF = 520
_BODY_MRTD_OFF = 136
_BODY_RTMR0_OFF = 328
_MEAS_LEN = 48
_RD_LEN = 64

_OID_SGX_EXTENSIONS = ObjectIdentifier("1.2.840.113741.1.13.1")
_OID_SGX_TCB = "1.2.840.113741.1.13.1.2"
_OID_SGX_PCEID = "1.2.840.113741.1.13.1.3"
_OID_SGX_FMSPC = "1.2.840.113741.1.13.1.4"


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _raw_pub(key: ec.EllipticCurvePublicKey) -> bytes:
    """64-byte X||Y (strip the 0x04 uncompressed-point prefix)."""
    pt = key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    assert pt[0] == 0x04 and len(pt) == 65
    return pt[1:]


def _sig_rs(privkey: ec.EllipticCurvePrivateKey, message: bytes) -> bytes:
    """Sign ``message`` (ECDSA-P256/SHA-256) and return the raw 64-byte r||s."""
    der = privkey.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# --- DER encoders for the SGX extension --------------------------------------------

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _der_tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(value)) + value


def _der_oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    first = 40 * parts[0] + parts[1]
    out = bytearray([first])
    for p in parts[2:]:
        if p == 0:
            out.append(0)
            continue
        stack = []
        while p:
            stack.append(p & 0x7F)
            p >>= 7
        stack.reverse()
        for i in range(len(stack) - 1):
            out.append(stack[i] | 0x80)
        out.append(stack[-1])
    return _der_tlv(0x06, bytes(out))


def _der_int(n: int) -> bytes:
    if n == 0:
        return _der_tlv(0x02, b"\x00")
    body = n.to_bytes((n.bit_length() + 8) // 8, "big")  # +8 keeps it positive
    return _der_tlv(0x02, body)


def _der_octets(b: bytes) -> bytes:
    return _der_tlv(0x04, b)


def _der_seq(*items: bytes) -> bytes:
    return _der_tlv(0x30, b"".join(items))


def build_sgx_extension_der(fmspc: bytes, tcb_comps: list[int], pcesvn: int,
                            cpusvn: bytes, pceid: bytes = b"\x00\x00") -> bytes:
    """Build the DER for the SGX extension value (the parser's exact inverse).

    SEQUENCE {
      SEQUENCE { OID 1.2.840.113741.1.13.1.4 (FMSPC), OCTET STRING fmspc }
      SEQUENCE { OID 1.2.840.113741.1.13.1.3 (PCEID), OCTET STRING pceid }
      SEQUENCE { OID 1.2.840.113741.1.13.1.2 (TCB), SEQUENCE {
          SEQUENCE { OID ...2.1, INTEGER comp1 } ... (16 of these)
          SEQUENCE { OID ...2.17, INTEGER pcesvn }
          SEQUENCE { OID ...2.18, OCTET STRING cpusvn }
      }}
    }
    """
    assert len(tcb_comps) == 16
    tcb_items = []
    for i, c in enumerate(tcb_comps, start=1):
        tcb_items.append(_der_seq(_der_oid(f"{_OID_SGX_TCB}.{i}"), _der_int(c)))
    tcb_items.append(_der_seq(_der_oid(f"{_OID_SGX_TCB}.17"), _der_int(pcesvn)))
    tcb_items.append(_der_seq(_der_oid(f"{_OID_SGX_TCB}.18"), _der_octets(cpusvn)))
    tcb_seq = _der_seq(_der_oid(_OID_SGX_TCB), _der_seq(*tcb_items))

    fmspc_seq = _der_seq(_der_oid(_OID_SGX_FMSPC), _der_octets(fmspc))
    pceid_seq = _der_seq(_der_oid(_OID_SGX_PCEID), _der_octets(pceid))
    return _der_seq(fmspc_seq, pceid_seq, tcb_seq)


# --- cert builders -----------------------------------------------------------------

def _name(cn: str) -> x509.Name:
    return x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, cn),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Intel Corporation"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Santa Clara"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "CA"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    ])


def _new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def make_root(cn: str = "Intel SGX Root CA", *, not_before=None, not_after=None):
    key = _new_key()
    nb = not_before or (_utcnow() - datetime.timedelta(days=1))
    na = not_after or (_utcnow() + datetime.timedelta(days=3650))
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(_name(cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_intermediate(parent_key, parent_cert, cn="Intel SGX PCK Processor CA"):
    key = _new_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(parent_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_utcnow() - datetime.timedelta(days=1))
        .not_valid_after(_utcnow() + datetime.timedelta(days=1825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(parent_key, hashes.SHA256())
    )
    return key, cert


def make_pck_leaf(parent_key, parent_cert, sgx_ext_der: bytes, cn="Intel SGX PCK Certificate",
                  *, not_before=None, not_after=None):
    key = _new_key()
    nb = not_before or (_utcnow() - datetime.timedelta(days=1))
    na = not_after or (_utcnow() + datetime.timedelta(days=365))
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(parent_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.UnrecognizedExtension(_OID_SGX_EXTENSIONS, sgx_ext_der), critical=False
        )
        .sign(parent_key, hashes.SHA256())
    )
    return key, cert


def make_tcb_signer(parent_key, parent_cert, cn="Intel SGX TCB Signing"):
    key = _new_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(parent_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_utcnow() - datetime.timedelta(days=1))
        .not_valid_after(_utcnow() + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(parent_key, hashes.SHA256())
    )
    return key, cert


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


# --- the full bundle ---------------------------------------------------------------

@dataclass
class SynthBundle:
    quote: bytes
    root_pem: str
    fmspc: str
    tcb_comps: list[int]
    pcesvn: int
    # keys/certs kept so negative tests can re-sign / tamper
    root_key: ec.EllipticCurvePrivateKey = field(repr=False, default=None)
    root_cert: x509.Certificate = field(repr=False, default=None)
    inter_key: ec.EllipticCurvePrivateKey = field(repr=False, default=None)
    inter_cert: x509.Certificate = field(repr=False, default=None)
    pck_key: ec.EllipticCurvePrivateKey = field(repr=False, default=None)
    pck_cert: x509.Certificate = field(repr=False, default=None)
    tcb_signer_key: ec.EllipticCurvePrivateKey = field(repr=False, default=None)
    tcb_signer_cert: x509.Certificate = field(repr=False, default=None)


def _build_td_body(report_data_digest: bytes) -> bytes:
    body = bytearray(TD_REPORT_BODY_LEN)
    body[_BODY_MRTD_OFF:_BODY_MRTD_OFF + _MEAS_LEN] = bytes([0x11]) * _MEAS_LEN
    for i in range(4):
        off = _BODY_RTMR0_OFF + i * _MEAS_LEN
        body[off:off + _MEAS_LEN] = bytes([0x20 + i]) * _MEAS_LEN
    rd = report_data_digest.ljust(_RD_LEN, b"\x00")
    body[_BODY_REPORT_DATA_OFF:_BODY_REPORT_DATA_OFF + _RD_LEN] = rd
    return bytes(body)


def _build_qe_report(binding32: bytes) -> bytes:
    """A 384-byte SGX report whose report_data[:32] == binding32 (rest zero)."""
    report = bytearray(SGX_REPORT_LEN)
    rd_off = SGX_REPORT_LEN - 64
    report[rd_off:rd_off + 32] = binding32
    # leave the trailing 32 bytes of report_data zero
    return bytes(report)


def build_quote(
    *,
    report_data_digest: bytes,
    fmspc: bytes = bytes.fromhex("00906ED50000"),
    tcb_comps: list[int] | None = None,
    pcesvn: int = 11,
    version: int = 4,
    pck_not_before=None,
    pck_not_after=None,
    qe_auth_data: bytes = b"\x00\x01",
) -> SynthBundle:
    """Build a fully-consistent synthetic v4 quote + the keys/certs used to sign it."""
    if tcb_comps is None:
        tcb_comps = [3, 3, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    root_key, root_cert = make_root()
    inter_key, inter_cert = make_intermediate(root_key, root_cert)
    sgx_ext = build_sgx_extension_der(fmspc, tcb_comps, pcesvn, cpusvn=bytes(16))
    pck_key, pck_cert = make_pck_leaf(
        inter_key, inter_cert, sgx_ext,
        not_before=pck_not_before, not_after=pck_not_after,
    )
    tcb_signer_key, tcb_signer_cert = make_tcb_signer(root_key, root_cert)

    # --- attestation key + quote signature -------------------------------------
    att_key = _new_key()
    att_pub_raw = _raw_pub(att_key.public_key())

    header = bytearray(QUOTE_V4_HEADER_LEN)
    header[0:2] = struct.pack("<H", version)
    header[2:4] = struct.pack("<H", 2)  # att_key_type = 2 (ECDSA-P256)
    td_body = _build_td_body(report_data_digest)
    signed_payload = bytes(header) + td_body
    quote_signature = _sig_rs(att_key, signed_payload)

    # --- QE report: bind att key, then sign the report with the PCK leaf -------
    binding = hashlib.sha256(att_pub_raw + qe_auth_data).digest()
    qe_report = _build_qe_report(binding)
    qe_report_signature = _sig_rs(pck_key, qe_report)

    # --- PCK cert chain PEM (leaf, intermediate, root) -------------------------
    cert_chain_pem = (_pem(pck_cert) + _pem(inter_cert) + _pem(root_cert)).encode("ascii")

    # --- assemble the ECDSA-256 signature data ---------------------------------
    sig_data = bytearray()
    sig_data += quote_signature                       # 64
    sig_data += att_pub_raw                            # 64
    sig_data += qe_report                              # 384
    sig_data += qe_report_signature                    # 64
    sig_data += struct.pack("<H", len(qe_auth_data))   # u16
    sig_data += qe_auth_data
    sig_data += struct.pack("<H", 5)                   # cert_key_type = 5 (PCK PEM chain)
    sig_data += struct.pack("<I", len(cert_chain_pem)) # cert_data_size
    sig_data += cert_chain_pem

    quote = signed_payload + struct.pack("<I", len(sig_data)) + bytes(sig_data)

    return SynthBundle(
        quote=bytes(quote),
        root_pem=_pem(root_cert),
        fmspc=fmspc.hex(),
        tcb_comps=tcb_comps,
        pcesvn=pcesvn,
        root_key=root_key, root_cert=root_cert,
        inter_key=inter_key, inter_cert=inter_cert,
        pck_key=pck_key, pck_cert=pck_cert,
        tcb_signer_key=tcb_signer_key, tcb_signer_cert=tcb_signer_cert,
    )


# --- collateral builders (TCB info + QE identity), Intel-signed -------------------

def _sign_payload_obj(signer_key, payload_obj: dict) -> tuple[str, str]:
    """Canonicalize the payload object and return (raw_json_str, signature_hex)."""
    raw = json.dumps(payload_obj, separators=(",", ":"), sort_keys=True)
    sig = _sig_rs(signer_key, raw.encode("utf-8"))
    return raw, sig.hex()


def build_tcb_info_response(
    bundle: SynthBundle,
    *,
    tcb_status: str = "UpToDate",
    fmspc: str | None = None,
    tcb_comps: list[int] | None = None,
    pcesvn: int | None = None,
) -> dict:
    """Build an Intel-signed tcbInfo PCS response keyed to this bundle's FMSPC.

    The single tcbLevel is set so the platform (bundle's PCK comps/pcesvn) MEETS it,
    yielding ``tcb_status``. Lower component values keep the platform at-or-above.
    """
    fmspc = fmspc if fmspc is not None else bundle.fmspc
    comps = tcb_comps if tcb_comps is not None else bundle.tcb_comps
    pcesvn = pcesvn if pcesvn is not None else bundle.pcesvn
    tcb_info = {
        "id": "TDX",
        "version": 3,
        "fmspc": fmspc,
        "pceId": "0000",
        "tcbType": 0,
        "tcbEvaluationDataNumber": 1,
        "tcbLevels": [
            {
                "tcb": {
                    "sgxtcbcomponents": [{"svn": c} for c in comps],
                    "pcesvn": pcesvn,
                },
                "tcbStatus": tcb_status,
                "tcbDate": "2024-01-01T00:00:00Z",
            }
        ],
    }
    raw, sig_hex = _sign_payload_obj(bundle.tcb_signer_key, tcb_info)
    chain_pem = _pem(bundle.tcb_signer_cert) + _pem(bundle.root_cert)
    return {
        "tcbInfo": tcb_info,
        "tcbInfo_raw": raw,
        "signature": sig_hex,
        "signing_cert_chain_pem": chain_pem,
    }


def build_qe_identity_response(bundle: SynthBundle) -> dict:
    """Build an Intel-signed enclaveIdentity (QE identity) PCS response."""
    enclave_identity = {
        "id": "TD_QE",
        "version": 2,
        "miscselect": "00000000",
        "miscselectMask": "FFFFFFFF",
        "attributes": "11000000000000000000000000000000",
        "mrsigner": "00" * 48,
        "isvprodid": 2,
        "tcbLevels": [
            {"tcb": {"isvsvn": 5}, "tcbStatus": "UpToDate", "tcbDate": "2024-01-01T00:00:00Z"}
        ],
    }
    raw, sig_hex = _sign_payload_obj(bundle.tcb_signer_key, enclave_identity)
    chain_pem = _pem(bundle.tcb_signer_cert) + _pem(bundle.root_cert)
    return {
        "enclaveIdentity": enclave_identity,
        "enclaveIdentity_raw": raw,
        "signature": sig_hex,
        "signing_cert_chain_pem": chain_pem,
    }


def make_pcs_transport(tcb_resp: dict, qe_resp: dict):
    """Return a pcs_transport(url) that dispatches on '/tcb' vs '/qe/identity'."""
    def _transport(url: str) -> dict:
        if "/tcb" in url:
            return tcb_resp
        if "/qe/identity" in url or "/qe" in url:
            return qe_resp
        raise AssertionError(f"unexpected PCS url {url!r}")
    return _transport
