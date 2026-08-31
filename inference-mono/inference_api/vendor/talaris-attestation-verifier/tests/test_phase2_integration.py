"""Phase 2 cross-examination: a full synthetic ``nvidia``-mode attestation document
must pass verify_attestation under PRODUCTION policy (required_modes={"nvidia"},
allow_insecure_dev=False) through BOTH real backends composed in the orchestrator:

  * the Intel TDX backend (real DCAP chain) against a synthetic, fully-signed v4 quote
    + Intel-signed collateral, pinned to the synthetic test root;
  * the NVIDIA NRAS backend (real EAT/JWS verification) against a test-signed EAT served
    via an injected JWKS.

report_data is computed by the REAL producer and binds every field (incl. gpu_hash), so
the document is internally consistent exactly as a genuine enclave would emit it. Then
each negative control must fail CLOSED.
"""

from __future__ import annotations

import json

import pytest

# producer (source of truth) — needs PYTHONPATH=inference-subnet-miner/src
producer_attestation = pytest.importorskip("tee_wrapper.security.attestation")
producer_report_data = producer_attestation.report_data
REPORT_DATA_DOMAIN_TAG = producer_attestation.REPORT_DATA_DOMAIN_TAG
REPORT_DATA_FIELDS = producer_attestation.REPORT_DATA_FIELDS

from talaris_attest import ExpectedClaims, verify_attestation  # noqa: E402
from talaris_attest.gpu import hash_gpu_uuids  # noqa: E402
from talaris_attest.backends.intel_pcs import IntelPcsTdxBackend  # noqa: E402
from talaris_attest.backends.nvidia_nras import NvidiaNrasBackend  # noqa: E402

import _tdx_synth as synth  # noqa: E402
import test_backends_nvidia_nras as nras_helpers  # noqa: E402

NONCE = bytes.fromhex("a1" * 32)
GPU_UUIDS = ["GPU-uuid-1", "GPU-uuid-2"]  # must match nras_helpers._good_claims submods
MINER_HASH = "aa" * 32
IDENTITY_FPR = "11" * 32
TLS_FPR = "bb" * 32
MODEL_HASH = "cc" * 32
ARGS_HASH = "dd" * 32
USAGE_CHAIN_HEAD = "ee" * 32


def _build_consistent_doc():
    """Build a synthetic nvidia doc + the two configured real backends.

    Returns (doc, nvidia_backend, intel_backend, expected_claims).
    """
    gpu_hash = hash_gpu_uuids(GPU_UUIDS)

    # 1) The REAL producer computes report_data binding all fields (incl. gpu_hash).
    rd = producer_report_data(
        nonce=NONCE,
        identity_fpr=IDENTITY_FPR,
        miner_hash=MINER_HASH,
        tls_fpr=TLS_FPR,
        model_hash=MODEL_HASH,
        usage_chain_head=USAGE_CHAIN_HEAD,
        args_hash=ARGS_HASH,
        gpu_hash=gpu_hash,
        usage_count=0,
        usage_total_tokens=0,
    )
    rd_hex = rd.hex()

    # 2) Synthetic, fully-signed TDX quote whose embedded report_data echoes rd, plus
    #    Intel-signed collateral; pin the synthetic root.
    bundle = synth.build_quote(report_data_digest=rd)
    pcs_transport = synth.make_pcs_transport(
        synth.build_tcb_info_response(bundle, tcb_status="UpToDate"),
        synth.build_qe_identity_response(bundle),
    )
    intel = IntelPcsTdxBackend(
        trusted_root_pem=bundle.root_pem,
        pcs_transport=pcs_transport,
        require_collateral=True,
    )

    # 3) Test-signed EAT (eat_nonce == rd hex, GPU UUIDs match) + injected JWKS.
    key, jwk = nras_helpers._make_es384()
    eat = nras_helpers._make_signed_eat(
        key, "ES384", "test-es384", nras_helpers._good_claims(rd_hex)
    )
    nvidia = NvidiaNrasBackend(
        transport=nras_helpers._transport_returning(eat),
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=nras_helpers._jwks_transport_for(jwk),
        expected_issuer=nras_helpers.ISSUER,
    )

    # 4) The full wire document, exactly as the GET /attestation router would emit it.
    doc = {
        "mode": "nvidia",
        "nonce": NONCE.hex(),
        "report_data": rd_hex,
        "public_bundle": {"verify_key": IDENTITY_FPR, "sealing_key": "22" * 32},
        "miner": MINER_HASH,
        "evidence": {
            "gpu_evidence": {"result_code": 0, "evidences": ["synthetic-gpu-evidence"]},
            "tdx_quote": {"quote": bundle.quote.hex()},
            "tdx_quote_absent": False,
        },
        "tls_cert_fingerprint": TLS_FPR,
        "model_hash": MODEL_HASH,
        "usage_chain_head": USAGE_CHAIN_HEAD,
        "args_hash": ARGS_HASH,
        "gpu_hash": gpu_hash,
        "gpu_uuids": list(GPU_UUIDS),
        "usage_count": 0,
        "usage_total_tokens": 0,
        "report_data_version": "v6",
        "report_data_domain_tag": REPORT_DATA_DOMAIN_TAG.decode(),
        "report_data_fields": list(REPORT_DATA_FIELDS),
        # router-injected
        "boot_nonce": "boot-nonce",
        "fingerprint": IDENTITY_FPR,
        "tls_pubkey_signature": None,
    }

    expected = ExpectedClaims(
        required_modes=frozenset({"nvidia"}),
        allow_insecure_dev=False,
        expected_miner_hash=MINER_HASH,
        nvidia_backend=nvidia,
        tdx_backend=intel,
        served_cert_der=None,  # TLS pin is informational here (tested separately)
    )
    return doc, nvidia, intel, expected, bundle


def test_full_nvidia_doc_verifies_under_production_policy():
    doc, _nvidia, _intel, expected, _bundle = _build_consistent_doc()
    result = verify_attestation(doc, expected, NONCE)
    assert result.ok, result.summary()
    names = {c.name: c.passed for c in result.checks}
    # The security-critical checks genuinely ran and passed:
    for required in ("mode", "nonce_echo", "report_data", "gpu_evidence",
                     "tdx_quote", "gpu_hash", "miner_hash"):
        assert names.get(required) is True, f"{required} did not pass: {result.summary()}"
    # No required check failed.
    assert result.failures == []


def test_dev_doc_rejected_under_production_policy():
    """The SAME document in dev mode must fail the mode gate (defense-in-depth)."""
    doc, _n, _i, expected, _b = _build_consistent_doc()
    doc["mode"] = "dev"
    result = verify_attestation(doc, expected, NONCE)
    assert not result.ok
    mode = next(c for c in result.checks if c.name == "mode")
    assert mode.passed is False


def test_tampered_tdx_quote_fails_closed():
    doc, _n, _i, expected, _b = _build_consistent_doc()
    q = bytearray(bytes.fromhex(doc["evidence"]["tdx_quote"]["quote"]))
    q[200] ^= 0x01  # flip a byte inside the signed payload / sig data
    doc["evidence"]["tdx_quote"]["quote"] = bytes(q).hex()
    result = verify_attestation(doc, expected, NONCE)
    assert not result.ok
    assert next(c for c in result.checks if c.name == "tdx_quote").passed is False


def test_tampered_eat_signature_fails_closed():
    doc, nvidia, _i, expected, _b = _build_consistent_doc()
    # Re-point the NRAS transport at an EAT with a corrupted signature.
    key, jwk = nras_helpers._make_es384()
    rd_hex = doc["report_data"]
    eat = nras_helpers._make_signed_eat(key, "ES384", "test-es384",
                                        nras_helpers._good_claims(rd_hex))
    head, payload, sig = eat.split(".")
    bad = f"{head}.{payload}.{sig[:-2] + ('AA' if sig[-2:] != 'AA' else 'BB')}"
    tampered_nvidia = NvidiaNrasBackend(
        transport=nras_helpers._transport_returning(bad),
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=nras_helpers._jwks_transport_for(jwk),
        expected_issuer=nras_helpers.ISSUER,
    )
    expected.nvidia_backend = tampered_nvidia
    result = verify_attestation(doc, expected, NONCE)
    assert not result.ok
    assert next(c for c in result.checks if c.name == "gpu_evidence").passed is False


def test_tdx_quote_against_unpinned_root_fails_closed():
    doc, _n, _i, expected, bundle = _build_consistent_doc()
    # Pin a DIFFERENT (freshly generated) Intel root than the one that signed the chain.
    _other_key, other_root = synth.make_root()
    from cryptography.hazmat.primitives import serialization

    other_root_pem = other_root.public_bytes(serialization.Encoding.PEM).decode()
    expected.tdx_backend = IntelPcsTdxBackend(
        trusted_root_pem=other_root_pem,
        pcs_transport=synth.make_pcs_transport(
            synth.build_tcb_info_response(bundle),
            synth.build_qe_identity_response(bundle),
        ),
        require_collateral=True,
    )
    result = verify_attestation(doc, expected, NONCE)
    assert not result.ok
    assert next(c for c in result.checks if c.name == "tdx_quote").passed is False


def test_report_data_field_tamper_fails_closed():
    """Changing a bound field WITHOUT recomputing report_data breaks the recompute."""
    doc, _n, _i, expected, _b = _build_consistent_doc()
    doc["model_hash"] = "00" * 32  # was MODEL_HASH; report_data no longer matches
    result = verify_attestation(doc, expected, NONCE)
    assert not result.ok
    assert next(c for c in result.checks if c.name == "report_data").passed is False
