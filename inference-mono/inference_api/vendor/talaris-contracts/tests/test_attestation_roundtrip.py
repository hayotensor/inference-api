"""Round-trip a REAL producer /attestation document through AttestationDocument.

Generates the document in-process via the actual TEE FastAPI app (dev mode,
in-memory usage db), parses it into the mirror model, dumps it, and asserts every
producer key survives — including the three router-injected top-level keys
(boot_nonce, fingerprint, tls_pubkey_signature).
"""

from __future__ import annotations

import secrets

import pytest

from talaris_contracts import (
    REPORT_DATA_DOMAIN_TAG,
    REPORT_DATA_FIELDS,
    WIRE_VERSION,
    AttestationDocument,
    report_data_inputs,
)

# These imports require PYTHONPATH=.../inference-subnet-miner/src
pytest.importorskip("tee_wrapper")
from fastapi.testclient import TestClient  # noqa: E402
from tee_wrapper.app import create_app  # noqa: E402
from tee_wrapper.security.attestation import (  # noqa: E402
    REPORT_DATA_DOMAIN_TAG as PROD_DOMAIN_TAG,
    REPORT_DATA_FIELDS as PROD_FIELDS,
    REPORT_DATA_VERSION as PROD_VERSION,
)


def _fetch_doc(tee_settings) -> dict:
    with TestClient(create_app(tee_settings())) as c:
        resp = c.get(f"/attestation?nonce={secrets.token_hex(16)}")
        assert resp.status_code == 200, resp.text
        return resp.json()


def test_constants_match_producer():
    assert WIRE_VERSION == PROD_VERSION
    assert REPORT_DATA_DOMAIN_TAG == PROD_DOMAIN_TAG.decode()
    assert list(REPORT_DATA_FIELDS) == list(PROD_FIELDS)


def test_injected_keys_present_in_producer_doc(tee_settings):
    raw = _fetch_doc(tee_settings)
    for k in ("boot_nonce", "fingerprint", "tls_pubkey_signature"):
        assert k in raw, f"producer doc missing injected key {k!r}"


def test_roundtrip_preserves_every_producer_key(tee_settings):
    raw = _fetch_doc(tee_settings)
    doc = AttestationDocument.model_validate(raw)
    dumped = doc.model_dump()

    # Every key the producer emitted must survive the round-trip.
    missing = set(raw) - set(dumped)
    assert not missing, f"keys dropped on round-trip: {missing}"

    # Values must match for every producer key (the mirror must not mutate them).
    for k, v in raw.items():
        assert dumped[k] == v, f"value changed for {k!r}: {dumped[k]!r} != {v!r}"


def test_known_fields_typed_correctly(tee_settings):
    raw = _fetch_doc(tee_settings)
    doc = AttestationDocument.model_validate(raw)

    assert doc.mode == "dev"
    assert doc.public_bundle.verify_key == raw["public_bundle"]["verify_key"]
    assert doc.public_bundle.sealing_key == raw["public_bundle"]["sealing_key"]
    assert doc.report_data_version == WIRE_VERSION
    assert doc.report_data_domain_tag == REPORT_DATA_DOMAIN_TAG
    assert doc.report_data_fields == list(REPORT_DATA_FIELDS)
    # dev-mode evidence carries an enclave signature over report_data.
    assert doc.dev_signature is not None
    assert doc.gpu_evidence is None  # dev mode has no GPU evidence
    assert doc.tdx_quote is None

    # The router injects these three at the top level.
    assert doc.boot_nonce == raw["boot_nonce"]
    assert doc.fingerprint == raw["fingerprint"]


def test_report_data_inputs_ordered_and_complete(tee_settings):
    raw = _fetch_doc(tee_settings)
    doc = AttestationDocument.model_validate(raw)
    inputs = report_data_inputs(doc)

    # Ordered map matches the producer field list (after domain tag), and the
    # domain_tag itself is the first published field name.
    assert list(inputs.keys()) == list(REPORT_DATA_FIELDS)
    assert inputs["domain_tag"] == REPORT_DATA_DOMAIN_TAG
    assert inputs["nonce"] == raw["nonce"]
    assert inputs["identity_fpr"] == raw["fingerprint"]
    # Integer fields stay None when absent (distinct from "0").
    assert inputs["usage_count"] == raw.get("usage_count")
    assert inputs["usage_total_tokens"] == raw.get("usage_total_tokens")


def test_extra_field_forward_compat_survives_roundtrip(tee_settings):
    raw = _fetch_doc(tee_settings)
    raw["some_future_field"] = {"nested": [1, 2, 3]}
    doc = AttestationDocument.model_validate(raw)
    dumped = doc.model_dump()
    assert dumped["some_future_field"] == {"nested": [1, 2, 3]}
