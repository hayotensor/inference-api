"""End-to-end verify_attestation behavior on a genuine dev document.

Covers the two independent dev gates:
  * a dev doc PASSES with allow_insecure_dev=True (required_modes={"dev"});
  * the SAME doc FAILS with default (production) ExpectedClaims on the mode gate;
  * a stub backend is REFUSED in production via backend_insecure_gate;
  * nonce / report_data tampering is caught.
"""

from __future__ import annotations

import pytest

from talaris_attest import ExpectedClaims, verify_attestation
from talaris_attest.backends.stub import StubGpuBackend, StubTdxBackend

from conftest import requires_producer


@requires_producer
def test_dev_doc_passes_with_insecure_dev(dev_doc, dev_nonce):
    exp = ExpectedClaims(
        required_modes=frozenset({"dev"}),
        allow_insecure_dev=True,
    )
    res = verify_attestation(dev_doc, exp, dev_nonce)
    assert res.ok, res.summary()
    names = {c.name for c in res.checks}
    # The core steps all ran.
    assert {"mode", "nonce_echo", "report_data", "gpu_evidence", "tdx_quote",
            "gpu_hash", "tls_pin"} <= names
    # report_data and mode passed.
    by_name = {c.name: c for c in res.checks}
    assert by_name["report_data"].passed
    assert by_name["mode"].passed


@requires_producer
def test_dev_doc_fails_default_production_claims(dev_doc, dev_nonce):
    """Default ExpectedClaims is production: required_modes={'nvidia'}, no dev override."""
    exp = ExpectedClaims()  # production defaults
    assert exp.required_modes == frozenset({"nvidia"})
    assert exp.allow_insecure_dev is False

    res = verify_attestation(dev_doc, exp, dev_nonce)
    assert not res.ok
    by_name = {c.name: c for c in res.checks}
    # Mode gate must be the (or a) failing check.
    assert by_name["mode"].passed is False
    assert "not in required_modes" in by_name["mode"].reason


@requires_producer
def test_stub_backend_refused_in_production(dev_doc, dev_nonce):
    """Explicitly handing stub backends to a production verify must be refused."""
    exp = ExpectedClaims(
        # Pretend the doc mode were allowed so we isolate the SECOND gate; we still keep
        # allow_insecure_dev False so the backend gate fires.
        required_modes=frozenset({"dev"}),
        allow_insecure_dev=False,
        nvidia_backend=StubGpuBackend(),
        tdx_backend=StubTdxBackend(),
    )
    res = verify_attestation(dev_doc, exp, dev_nonce)
    by_name = {c.name: c for c in res.checks}
    assert "backend_insecure_gate" in by_name
    assert by_name["backend_insecure_gate"].passed is False
    assert not res.ok


@requires_producer
def test_nonce_mismatch_fails(dev_doc):
    exp = ExpectedClaims(required_modes=frozenset({"dev"}), allow_insecure_dev=True)
    wrong_nonce = bytes.fromhex("cc" * 32)
    res = verify_attestation(dev_doc, exp, wrong_nonce)
    by_name = {c.name: c for c in res.checks}
    assert by_name["nonce_echo"].passed is False
    assert not res.ok


@requires_producer
def test_report_data_tamper_fails(dev_doc, dev_nonce):
    """Flipping a bound field without re-signing breaks the report_data match."""
    exp = ExpectedClaims(required_modes=frozenset({"dev"}), allow_insecure_dev=True)
    tampered = dict(dev_doc, model_hash="deadbeef" * 8)  # was None
    res = verify_attestation(tampered, exp, dev_nonce)
    by_name = {c.name: c for c in res.checks}
    assert by_name["report_data"].passed is False
    assert not res.ok


@requires_producer
def test_unknown_mode_fails_closed(dev_doc, dev_nonce):
    exp = ExpectedClaims(required_modes=frozenset({"dev"}), allow_insecure_dev=True)
    weird = dict(dev_doc, mode="totally-unknown")
    res = verify_attestation(weird, exp, dev_nonce)
    by_name = {c.name: c for c in res.checks}
    assert by_name["mode"].passed is False
    assert "unknown attestation mode" in by_name["mode"].reason


@requires_producer
def test_accepts_model_dump_object(dev_doc, dev_nonce):
    """Duck-typing: an object exposing .model_dump() is accepted like a dict."""
    class FakeContractsDoc:
        def __init__(self, d):
            self._d = d

        def model_dump(self):
            return dict(self._d)

    exp = ExpectedClaims(required_modes=frozenset({"dev"}), allow_insecure_dev=True)
    res = verify_attestation(FakeContractsDoc(dev_doc), exp, dev_nonce)
    assert res.ok, res.summary()
