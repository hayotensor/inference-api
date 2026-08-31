"""Stub backend unit tests — they accept ONLY the enclave dev self-signature.

StubGpuBackend must:
  * accept a valid DevGpuEvidence whose dev_signature is a real Ed25519 signature over
    report_data under the enclave verify key, and whose report_data matches the expected
    nonce hex;
  * reject a bad signature, a wrong report_data, or real (non-dev) evidence;
  * be marked is_secure == False so verify.py refuses it in production.

StubTdxBackend accepts a None/absent dev quote with no chain verification.
"""

from __future__ import annotations

from nacl.signing import SigningKey

from talaris_attest.backends.stub import DevGpuEvidence, StubGpuBackend, StubTdxBackend


def _signed_evidence(report_data: bytes, *, good_sig: bool = True):
    sk = SigningKey.generate()
    vk = bytes(sk.verify_key)
    sig = sk.sign(report_data).signature
    if not good_sig:
        sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    return DevGpuEvidence(
        verify_key_bytes=vk,
        dev_signature_hex=sig.hex(),
        report_data=report_data,
        gpu_uuids=("GPU-aaaa", "GPU-bbbb"),
    )


def test_stub_gpu_accepts_valid_self_signature():
    rd = bytes.fromhex("12" * 32)
    ev = _signed_evidence(rd)
    res = StubGpuBackend().verify(ev, rd.hex())
    assert res.verified is True
    assert set(res.measured_gpu_uuids) == {"GPU-aaaa", "GPU-bbbb"}


def test_stub_gpu_rejects_bad_signature():
    rd = bytes.fromhex("12" * 32)
    ev = _signed_evidence(rd, good_sig=False)
    res = StubGpuBackend().verify(ev, rd.hex())
    assert res.verified is False


def test_stub_gpu_rejects_nonce_mismatch():
    rd = bytes.fromhex("12" * 32)
    ev = _signed_evidence(rd)
    res = StubGpuBackend().verify(ev, ("99" * 32))
    assert res.verified is False
    assert "report_data" in res.reason


def test_stub_gpu_rejects_real_evidence_shape():
    """A non-DevGpuEvidence (e.g. a raw nvattest payload dict) must be refused."""
    res = StubGpuBackend().verify({"evidences": [{"some": "real-blob"}]}, "00" * 32)
    assert res.verified is False


def test_stub_gpu_is_insecure():
    assert StubGpuBackend.is_secure is False


def test_stub_tdx_accepts_absent_quote():
    assert StubTdxBackend().verify(None, b"x").verified is True
    assert StubTdxBackend().verify({"quote": None}, b"x").verified is True


def test_stub_tdx_is_insecure():
    assert StubTdxBackend.is_secure is False
