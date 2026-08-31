"""TLS-pin verification must be byte-exact with the producer's SPKI binding.

We generate an enclave cert via the producer's ``generate_enclave_cert`` (the real
material a running enclave would attest), then:
  * the verifier's verify_tls_pubkey_signature accepts the genuine binding;
  * flipping one byte of the signature makes it fail;
  * cert_fingerprint_sha256 equals the producer's reported fingerprint;
  * verify_tls_pin enforces BOTH the SPKI signature and the fingerprint match;
  * the offline TLS-pin step in verify_attestation passes when served_cert_der is supplied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from talaris_attest import ExpectedClaims, verify_attestation
from talaris_attest.tls import (
    cert_fingerprint_sha256,
    verify_tls_pin,
    verify_tls_pubkey_signature,
)

from conftest import requires_producer


def _make_enclave_cert(tmp_path: Path):
    """Generate a fresh enclave-bound cert; return (der, fingerprint, sig, verify_key)."""
    import ssl

    from tee_wrapper.security.identity import EnclaveIdentity
    from tee_wrapper.security.tls import generate_enclave_cert

    identity = EnclaveIdentity.generate()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    material = generate_enclave_cert(str(cert_path), str(key_path), identity)
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    return der, material.fingerprint, material.pubkey_signature, identity.verify_key_bytes


@requires_producer
def test_tls_pubkey_signature_valid(tmp_path):
    der, fpr, sig, vk = _make_enclave_cert(tmp_path)
    assert verify_tls_pubkey_signature(der, sig, vk) is True
    assert cert_fingerprint_sha256(der) == fpr


@requires_producer
def test_flipped_signature_fails(tmp_path):
    der, fpr, sig, vk = _make_enclave_cert(tmp_path)
    sig_bytes = bytearray.fromhex(sig)
    sig_bytes[0] ^= 0xFF  # flip the first byte
    tampered = sig_bytes.hex()
    assert verify_tls_pubkey_signature(der, tampered, vk) is False


@requires_producer
def test_wrong_verify_key_fails(tmp_path):
    der, fpr, sig, vk = _make_enclave_cert(tmp_path)
    wrong_vk = bytes(32)  # all-zero key
    assert verify_tls_pubkey_signature(der, sig, wrong_vk) is False


@requires_producer
def test_verify_tls_pin_requires_both(tmp_path):
    der, fpr, sig, vk = _make_enclave_cert(tmp_path)

    ok, _ = verify_tls_pin(der, fpr, sig, vk)
    assert ok is True

    # Wrong fingerprint -> fail even with a valid SPKI signature.
    bad_fpr = ("0" if fpr[0] != "0" else "1") + fpr[1:]
    ok2, reason2 = verify_tls_pin(der, bad_fpr, sig, vk)
    assert ok2 is False and "fingerprint" in reason2

    # Empty signature -> fail even with the right fingerprint.
    ok3, reason3 = verify_tls_pin(der, fpr, "", vk)
    assert ok3 is False


@requires_producer
def test_offline_tls_pin_in_verify_attestation(dev_doc, dev_nonce, tmp_path):
    """When the caller supplies served_cert_der, the tls_pin step runs offline.

    The dev_doc's tls_cert_fingerprint corresponds to the cert the producer generated for
    THAT enclave. We can't reach into that enclave's cert from here, so we assert the
    NEGATIVE direction: supplying an UNRELATED cert makes tls_pin fail (fingerprint
    mismatch), proving the offline step is actually enforced rather than skipped.
    """
    der, _fpr, _sig, _vk = _make_enclave_cert(tmp_path)
    exp = ExpectedClaims(
        required_modes=frozenset({"dev"}),
        allow_insecure_dev=True,
        served_cert_der=der,  # an unrelated cert
    )
    res = verify_attestation(dev_doc, exp, dev_nonce)
    by_name = {c.name: c for c in res.checks}
    assert by_name["tls_pin"].passed is False
    assert not res.ok


@requires_producer
def test_offline_tls_pin_passes_for_matching_cert(tmp_path, dev_nonce):
    """Construct a synthetic doc whose tls fields match a real enclave cert and pin it.

    This exercises the SUCCESS path of the offline tls_pin step: a document whose
    tls_cert_fingerprint and tls_pubkey_signature genuinely correspond to the supplied
    served cert (and whose verify_key is that enclave's key) passes the pin.
    """
    import ssl

    from tee_wrapper.security.identity import EnclaveIdentity
    from tee_wrapper.security.tls import generate_enclave_cert
    from talaris_attest.report_data import recompute
    from talaris_attest.docview import DocView

    identity = EnclaveIdentity.generate()
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    material = generate_enclave_cert(str(cert_path), str(key_path), identity)
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())

    nonce = dev_nonce
    fields = [
        "domain_tag", "nonce", "identity_fpr", "miner_hash", "tls_fpr", "model_hash",
        "usage_chain_head", "args_hash", "gpu_hash", "usage_count", "usage_total_tokens",
    ]
    doc = {
        "mode": "dev",
        "nonce": nonce.hex(),
        "fingerprint": identity.fingerprint(),
        "public_bundle": identity.public_bundle(),
        "miner": None,
        "tls_cert_fingerprint": material.fingerprint,
        "tls_pubkey_signature": material.pubkey_signature,
        "model_hash": None, "usage_chain_head": "", "args_hash": None, "gpu_hash": None,
        "gpu_uuids": [], "usage_count": None, "usage_total_tokens": None,
        "report_data_version": "v6",
        "report_data_domain_tag": "tee-wrapper/report-data/v6",
        "report_data_fields": fields,
        "evidence": {"dev_signature": identity.sign(b"").hex()},  # placeholder, GPU off
    }
    # Make report_data consistent and sign it so gpu_evidence also passes.
    rd = recompute(DocView(doc))
    doc["report_data"] = rd.hex()
    doc["evidence"]["dev_signature"] = identity.sign(rd).hex()

    exp = ExpectedClaims(
        required_modes=frozenset({"dev"}),
        allow_insecure_dev=True,
        served_cert_der=der,
    )
    res = verify_attestation(doc, exp, nonce)
    by_name = {c.name: c for c in res.checks}
    assert by_name["tls_pin"].passed is True, res.summary()
    assert res.ok, res.summary()
