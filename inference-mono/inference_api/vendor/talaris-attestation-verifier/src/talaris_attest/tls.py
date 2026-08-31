"""TLS-pin verification — byte-exact mirror of the producer's TLS-SPKI binding.

Single source of truth: ``tee_wrapper.security.tls`` —
  * TLS_SPKI_DOMAIN_TAG = b"tee-wrapper/tls-spki/v1"
  * the binding message is the domain tag concatenated with the cert's DER SPKI.
  * cert fingerprint = sha256(cert DER) hex.

This module re-implements those checks so the verifier needs no producer import:

  verify_tls_pubkey_signature(cert_der, pubkey_signature_hex, enclave_verify_key_bytes)
      Verify the enclave Ed25519 signature over TLS_SPKI_DOMAIN_TAG + SPKI_DER.

  cert_fingerprint_sha256(cert_der) -> hex sha256(DER).

  spki_der(cert_der) -> the cert's DER SubjectPublicKeyInfo bytes.
"""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path
from typing import Union

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

# Mirror of tee_wrapper.security.tls.TLS_SPKI_DOMAIN_TAG. Kept as a literal so this
# package has no producer dependency.
TLS_SPKI_DOMAIN_TAG = b"tee-wrapper/tls-spki/v1"


def _load_cert_der(cert_der_or_path: Union[bytes, bytearray, str]) -> bytes:
    """Return DER bytes from either raw DER, or a path to a PEM cert file."""
    if isinstance(cert_der_or_path, (bytes, bytearray)):
        return bytes(cert_der_or_path)
    pem = Path(cert_der_or_path).read_text()
    return ssl.PEM_cert_to_DER_cert(pem)


def spki_der(cert_der: bytes) -> bytes:
    """Return the cert's DER-encoded SubjectPublicKeyInfo (mirror of cert_spki_der)."""
    cert = x509.load_der_x509_certificate(cert_der)
    return cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _spki_binding_message(spki: bytes) -> bytes:
    """Domain-separated message: b"tee-wrapper/tls-spki/v1" + cert SPKI DER."""
    return TLS_SPKI_DOMAIN_TAG + spki


def cert_fingerprint_sha256(cert_der: bytes) -> str:
    """sha256(cert DER) hex — the fingerprint committed into report_data as tls_fpr."""
    return hashlib.sha256(cert_der).hexdigest()


def verify_tls_pubkey_signature(
    cert_der_or_path: Union[bytes, bytearray, str],
    pubkey_signature_hex: str,
    enclave_verify_key_bytes: bytes,
) -> bool:
    """Verify the enclave Ed25519 signature over TLS_SPKI_DOMAIN_TAG + cert SPKI DER.

    Returns True iff ``pubkey_signature_hex`` is a valid signature, under the attested
    enclave verify key, over the domain-separated binding message. Byte-exact mirror of
    ``tee_wrapper.security.tls.verify_cert_binding``.
    """
    try:
        der = _load_cert_der(cert_der_or_path)
        spki = spki_der(der)
    except Exception:
        return False

    if not isinstance(pubkey_signature_hex, str):
        return False
    try:
        signature = bytes.fromhex(pubkey_signature_hex)
    except ValueError:
        return False

    try:
        VerifyKey(enclave_verify_key_bytes).verify(_spki_binding_message(spki), signature)
        return True
    except (BadSignatureError, ValueError):
        return False


def verify_tls_pin(
    cert_der: bytes,
    expected_fingerprint: str,
    pubkey_signature_hex: str,
    enclave_verify_key_bytes: bytes,
) -> tuple[bool, str]:
    """Full TLS pin: SPKI signature valid AND sha256(cert DER) == expected fingerprint.

    Returns (ok, reason). Both conditions are REQUIRED:
      * the enclave vouched for THIS cert's public key (SPKI signature), and
      * the served cert is exactly the one attested (fingerprint match).
    """
    actual_fpr = cert_fingerprint_sha256(cert_der)
    if expected_fingerprint is None:
        return False, "document has no tls_cert_fingerprint to pin against"
    if actual_fpr != expected_fingerprint:
        return (
            False,
            f"served cert fingerprint {actual_fpr} != attested {expected_fingerprint}",
        )
    if not pubkey_signature_hex:
        return False, "document has no tls_pubkey_signature (cannot prove SPKI binding)"
    if not verify_tls_pubkey_signature(
        cert_der, pubkey_signature_hex, enclave_verify_key_bytes
    ):
        return False, "enclave SPKI binding signature invalid"
    return True, "TLS cert pinned: SPKI signature valid and fingerprint matches"
