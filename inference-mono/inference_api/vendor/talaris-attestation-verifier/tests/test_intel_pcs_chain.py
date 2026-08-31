"""Offline known-answer tests for the REAL Intel DCAP / PCS chain verification.

These tests build a structurally-real, fully-signed synthetic TDX v4 quote (see
``_tdx_synth``) plus Intel-signed collateral, pin a SYNTHETIC test root, and assert:

  * the positive path VERIFIES end-to-end (signatures, att-key binding, PCK chain to the
    pinned root, Intel-signed TCB info + QE identity, allowed TCB status);
  * each negative control fails CLOSED with a meaningful reason: tampered quote signature,
    tampered QE report signature, broken att-key binding, an unpinned/wrong root, an
    expired PCK cert, OutOfDate / Revoked TCB status, missing collateral with
    require_collateral=True, and a report_data echo mismatch.

Pure cryptography, no hardware, no network.
"""

from __future__ import annotations

import datetime

import _tdx_synth as S
from cryptography import x509
from talaris_attest.backends.intel_pcs import (
    DEFAULT_ALLOWED_TCB_STATUSES,
    INTEL_SGX_ROOT_CA_PEM,
    IntelPcsTdxBackend,
)

DIGEST = bytes.fromhex("ab" * 32)


def _backend(bundle, transport, **kw):
    return IntelPcsTdxBackend(
        trusted_root_pem=bundle.root_pem,
        pcs_transport=transport,
        **kw,
    )


def _good_transport(bundle, *, tcb_status="UpToDate"):
    tcb = S.build_tcb_info_response(bundle, tcb_status=tcb_status)
    qe = S.build_qe_identity_response(bundle)
    return S.make_pcs_transport(tcb, qe)


# --------------------------------------------------------------------------------- #
# Positive path
# --------------------------------------------------------------------------------- #

def test_full_chain_verifies():
    b = S.build_quote(report_data_digest=DIGEST)
    res = _backend(b, _good_transport(b)).verify(b.quote.hex(), DIGEST)
    assert res.verified is True, res.reason
    assert "DCAP chain verified" in (res.reason or "")
    assert res.measurements is not None
    assert res.measurements["mrtd"] == "11" * 48


def test_full_chain_verifies_via_producer_dict():
    b = S.build_quote(report_data_digest=DIGEST)
    res = _backend(b, _good_transport(b)).verify(
        {"quote": b.quote.hex(), "format": "tdx_quote_v4"}, DIGEST
    )
    assert res.verified is True, res.reason


def test_sw_hardening_needed_is_allowed_by_default():
    b = S.build_quote(report_data_digest=DIGEST)
    t = _good_transport(b, tcb_status="SWHardeningNeeded")
    res = _backend(b, t).verify(b.quote.hex(), DIGEST)
    assert res.verified is True, res.reason


# --------------------------------------------------------------------------------- #
# Negative controls — each must fail CLOSED
# --------------------------------------------------------------------------------- #

def test_tampered_quote_signature_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    q = bytearray(b.quote)
    # quote_signature is the first 64 bytes of sig data, which starts at 632+4.
    sig_off = 632 + 4
    q[sig_off] ^= 0xFF  # flip a byte of r
    res = _backend(b, _good_transport(b)).verify(bytes(q).hex(), DIGEST)
    assert res.verified is False
    assert "quote signature" in (res.reason or "")


def test_tampered_qe_report_signature_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    q = bytearray(b.quote)
    # sig data: quote_sig(64) att_key(64) qe_report(384) qe_report_sig(64) ...
    qe_sig_off = 632 + 4 + 64 + 64 + 384
    q[qe_sig_off] ^= 0xFF
    res = _backend(b, _good_transport(b)).verify(bytes(q).hex(), DIGEST)
    assert res.verified is False
    assert "QE report signature" in (res.reason or "")


def test_broken_att_key_binding_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    q = bytearray(b.quote)
    # Flip a byte of qe_report.report_data[:32] (the binding hash) WITHOUT changing the
    # qe_report_signature would already break the QE sig; instead we flip a byte of the
    # attestation key. That breaks BOTH the quote sig and the binding — we assert it
    # fails. To isolate the binding specifically, flip a qe_auth_data byte (below).
    # Here: flip att_key X so the binding SHA256(att_key||auth) no longer matches.
    att_off = 632 + 4 + 64  # after quote_signature
    q[att_off] ^= 0xFF
    res = _backend(b, _good_transport(b)).verify(bytes(q).hex(), DIGEST)
    assert res.verified is False
    # Flipping the att-key bytes can either make it an invalid EC point or (if still a
    # valid point) break the quote signature and the binding — all fail closed.
    assert (
        "attestation key" in (res.reason or "")
        or "quote signature" in (res.reason or "")
        or "not bound to the QE report" in (res.reason or "")
    )


def test_broken_binding_via_qe_auth_data_fails():
    """Isolate the att-key<->QE binding: tamper qe_auth_data so the binding hash differs.

    qe_auth_data is included in SHA256(att_key||qe_auth_data); changing it (without
    re-deriving the QE report_data) breaks ONLY the binding check, not the quote/QE sigs.
    """
    b = S.build_quote(report_data_digest=DIGEST, qe_auth_data=b"\x00\x01")
    q = bytearray(b.quote)
    # qe_auth_data sits after: quote_sig(64) att_key(64) qe_report(384) qe_report_sig(64)
    # then u16 len (2) then the data. len is 2 -> data is 2 bytes.
    auth_data_off = 632 + 4 + 64 + 64 + 384 + 64 + 2
    q[auth_data_off] ^= 0xFF
    res = _backend(b, _good_transport(b)).verify(bytes(q).hex(), DIGEST)
    assert res.verified is False
    assert "not bound to the QE report" in (res.reason or "")


def test_wrong_unpinned_root_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    # Pin a DIFFERENT (independent) root than the one that signed the chain.
    other_key, other_cert = S.make_root(cn="Intel SGX Root CA")
    other_pem = other_cert.public_bytes(
        __import__("cryptography.hazmat.primitives.serialization",
                   fromlist=["Encoding"]).Encoding.PEM
    ).decode()
    backend = IntelPcsTdxBackend(
        trusted_root_pem=other_pem, pcs_transport=_good_transport(b)
    )
    res = backend.verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "PCK chain" in (res.reason or "")


def test_production_default_pins_real_intel_root_and_rejects_synthetic():
    """The production default (no trusted_root_pem) pins the REAL Intel SGX Root CA, so a
    synthetic-root quote fails closed at the PCK chain step."""
    b = S.build_quote(report_data_digest=DIGEST)
    backend = IntelPcsTdxBackend(pcs_transport=_good_transport(b))  # defaults
    assert backend.require_chain is True
    assert backend.require_collateral is True
    res = backend.verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "PCK chain" in (res.reason or "")
    # Sanity: the bundled constant is the real, parseable Intel root.
    real = x509.load_pem_x509_certificate(INTEL_SGX_ROOT_CA_PEM.encode())
    assert real.subject.rfc4514_string().endswith("CN=Intel SGX Root CA")


def test_expired_pck_cert_fails():
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    b = S.build_quote(
        report_data_digest=DIGEST,
        pck_not_before=past - datetime.timedelta(days=10),
        pck_not_after=past,  # already expired
    )
    res = _backend(b, _good_transport(b)).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "validity period" in (res.reason or "") or "PCK chain" in (res.reason or "")


def test_out_of_date_tcb_status_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    t = _good_transport(b, tcb_status="OutOfDate")
    res = _backend(b, t).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "TCB" in (res.reason or "")
    assert "OutOfDate" in (res.reason or "")


def test_revoked_tcb_status_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    t = _good_transport(b, tcb_status="Revoked")
    res = _backend(b, t).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "Revoked" in (res.reason or "")


def test_platform_below_all_levels_is_out_of_date():
    """If the TCB info's only level requires HIGHER comps than the platform reports, the
    platform meets no level -> OutOfDate (fail closed)."""
    b = S.build_quote(report_data_digest=DIGEST)
    higher = [c + 10 for c in b.tcb_comps]
    tcb = S.build_tcb_info_response(b, tcb_status="UpToDate", tcb_comps=higher)
    qe = S.build_qe_identity_response(b)
    res = _backend(b, S.make_pcs_transport(tcb, qe)).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "OutOfDate" in (res.reason or "") or "below every level" in (res.reason or "")


def test_fmspc_mismatch_in_tcb_info_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    # Sign a tcbInfo whose FMSPC differs from the PCK leaf's.
    tcb = S.build_tcb_info_response(b, fmspc="ffffffffffff")
    qe = S.build_qe_identity_response(b)
    res = _backend(b, S.make_pcs_transport(tcb, qe)).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "FMSPC" in (res.reason or "")


def test_collateral_signed_by_wrong_key_fails():
    """TCB info whose signature does not verify under the (root-chained) signer fails."""
    b = S.build_quote(report_data_digest=DIGEST)
    tcb = S.build_tcb_info_response(b)
    # Corrupt the signature hex (flip a nibble).
    sig = bytearray(bytes.fromhex(tcb["signature"]))
    sig[0] ^= 0xFF
    tcb["signature"] = sig.hex()
    qe = S.build_qe_identity_response(b)
    res = _backend(b, S.make_pcs_transport(tcb, qe)).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "TCB info collateral invalid" in (res.reason or "")


def test_collateral_signer_not_chained_to_root_fails():
    """A TCB-signing cert that does NOT chain to the pinned root is rejected."""
    b = S.build_quote(report_data_digest=DIGEST)
    # Build a rogue root + signer; sign the tcbInfo with the rogue signer.
    rogue_root_key, rogue_root_cert = S.make_root(cn="Rogue Root")
    rogue_signer_key, rogue_signer_cert = S.make_tcb_signer(rogue_root_key, rogue_root_cert)
    # Replace the bundle's signer with the rogue one for the build.
    b.tcb_signer_key = rogue_signer_key
    b.tcb_signer_cert = rogue_signer_cert
    tcb = S.build_tcb_info_response(b)
    # The chain pem now references the rogue root, not the pinned (real bundle) root.
    qe = S.build_qe_identity_response(b)
    res = _backend(b, S.make_pcs_transport(tcb, qe)).verify(b.quote.hex(), DIGEST)
    assert res.verified is False
    assert "collateral" in (res.reason or "").lower()


def test_missing_collateral_fails_closed_when_required():
    b = S.build_quote(report_data_digest=DIGEST)

    def _broken_transport(url):
        raise RuntimeError("PCS unreachable")

    res = _backend(b, _broken_transport, require_collateral=True).verify(
        b.quote.hex(), DIGEST
    )
    assert res.verified is False
    assert "collateral fetch failed" in (res.reason or "")
    assert "require_collateral=True" in (res.reason or "")


def test_missing_collateral_allowed_when_not_required():
    """require_collateral=False is the documented (NOT production-safe) chain-only mode:
    signatures + PCK chain still verify, missing collateral is tolerated."""
    b = S.build_quote(report_data_digest=DIGEST)

    def _broken_transport(url):
        raise RuntimeError("PCS unreachable")

    res = _backend(b, _broken_transport, require_collateral=False).verify(
        b.quote.hex(), DIGEST
    )
    assert res.verified is True, res.reason
    assert "collateral SKIPPED" in (res.reason or "")


def test_report_data_echo_mismatch_fails_before_chain():
    b = S.build_quote(report_data_digest=DIGEST)
    res = _backend(b, _good_transport(b)).verify(b.quote.hex(), bytes.fromhex("cd" * 32))
    assert res.verified is False
    assert "echo" in (res.reason or "")


def test_truncated_signature_data_fails():
    b = S.build_quote(report_data_digest=DIGEST)
    # Chop off the signature data (keep header+body+len prefix only -> truncated).
    truncated = b.quote[:632 + 4 + 10]
    res = _backend(b, _good_transport(b)).verify(truncated.hex(), DIGEST)
    assert res.verified is False
    assert "parse" in (res.reason or "").lower() or "truncated" in (res.reason or "").lower()


def test_allowed_tcb_statuses_policy_is_configurable():
    """Tighten the policy to UpToDate-only: SWHardeningNeeded then fails."""
    b = S.build_quote(report_data_digest=DIGEST)
    t = _good_transport(b, tcb_status="SWHardeningNeeded")
    res = _backend(b, t, allowed_tcb_statuses=frozenset({"UpToDate"})).verify(
        b.quote.hex(), DIGEST
    )
    assert res.verified is False
    assert "SWHardeningNeeded" in (res.reason or "")


def test_default_allowed_statuses_constant():
    assert DEFAULT_ALLOWED_TCB_STATUSES == frozenset({"UpToDate", "SWHardeningNeeded"})
