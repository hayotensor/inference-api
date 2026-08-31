"""report_data recompute must be byte-exact with the producer.

A wrong byte here makes EVERY real attestation fail, so this is the highest-priority test:
  * the verifier's recompute() equals doc.report_data for a genuine dev fixture;
  * it also equals the producer's own report_data() output (direct cross-check);
  * tampering any single bound field flips the digest;
  * the None-vs-0 integer-field distinction is preserved;
  * an unknown report_data_version is rejected (fail closed).
"""

from __future__ import annotations

import pytest

from talaris_attest.docview import DocView
from talaris_attest.errors import UnsupportedReportDataVersion
from talaris_attest.report_data import recompute

from conftest import requires_producer


@requires_producer
def test_recompute_matches_dev_fixture(dev_doc):
    """recompute() over a genuine dev doc equals the document's report_data."""
    got = recompute(DocView(dev_doc)).hex()
    assert got == dev_doc["report_data"]


@requires_producer
def test_recompute_matches_producer_directly():
    """The verifier digest equals the producer's report_data() across field combos."""
    from tee_wrapper.security.attestation import report_data as producer_rd

    nonce = bytes.fromhex("3c" * 32)
    ident = "ab" * 32
    base_fields = [
        "domain_tag", "nonce", "identity_fpr", "miner_hash", "tls_fpr", "model_hash",
        "usage_chain_head", "args_hash", "gpu_hash", "usage_count", "usage_total_tokens",
    ]

    def doc_from(miner, tls, model, uch, args, gpu, ucount, utotal):
        return {
            "nonce": nonce.hex(), "fingerprint": ident, "miner": miner,
            "tls_cert_fingerprint": tls, "model_hash": model, "usage_chain_head": uch,
            "args_hash": args, "gpu_hash": gpu, "usage_count": ucount,
            "usage_total_tokens": utotal, "report_data_version": "v6",
            "report_data_domain_tag": "tee-wrapper/report-data/v6",
            "report_data_fields": base_fields,
        }

    combos = [
        (None, None, None, "", None, None, None, None),
        ("miner1", "tlsfpr", "mh", "uch", "ah", "gh", 0, 0),
        ("miner1", "tlsfpr", "mh", "uch", "ah", "gh", 7, 1234),
        (None, None, None, None, None, None, 0, None),  # 0 vs None mix
    ]
    for c in combos:
        prod = producer_rd(nonce, ident, *c)
        mine = recompute(DocView(doc_from(*c)))
        assert prod == mine, f"mismatch for {c}"


@requires_producer
def test_tamper_each_field_flips_digest(dev_doc):
    """Mutating any bound field changes the recomputed digest (so it stops matching)."""
    baseline = recompute(DocView(dev_doc)).hex()
    assert baseline == dev_doc["report_data"]

    # nonce is committed as raw bytes; a different nonce must change the digest.
    tampered_nonce = dict(dev_doc, nonce="bb" * 32)
    assert recompute(DocView(tampered_nonce)).hex() != baseline

    # identity fingerprint
    fp = dev_doc["fingerprint"]
    flipped_fp = ("0" if fp[0] != "0" else "1") + fp[1:]
    assert recompute(DocView(dict(dev_doc, fingerprint=flipped_fp))).hex() != baseline

    # string field that was None -> set it
    assert recompute(DocView(dict(dev_doc, model_hash="deadbeef"))).hex() != baseline

    # tls fingerprint
    tls = dev_doc["tls_cert_fingerprint"]
    flipped_tls = ("0" if tls[0] != "0" else "1") + tls[1:]
    assert recompute(
        DocView(dict(dev_doc, tls_cert_fingerprint=flipped_tls))
    ).hex() != baseline


def test_zero_distinct_from_none():
    """usage_count/usage_total_tokens: 0 must encode differently from absent (None)."""
    base_fields = [
        "domain_tag", "nonce", "identity_fpr", "miner_hash", "tls_fpr", "model_hash",
        "usage_chain_head", "args_hash", "gpu_hash", "usage_count", "usage_total_tokens",
    ]
    common = {
        "nonce": "11" * 32, "fingerprint": "22" * 32, "miner": None,
        "tls_cert_fingerprint": None, "model_hash": None, "usage_chain_head": "",
        "args_hash": None, "gpu_hash": None, "report_data_version": "v6",
        "report_data_domain_tag": "tee-wrapper/report-data/v6",
        "report_data_fields": base_fields,
    }
    none_doc = dict(common, usage_count=None, usage_total_tokens=None)
    zero_doc = dict(common, usage_count=0, usage_total_tokens=0)
    assert recompute(DocView(none_doc)) != recompute(DocView(zero_doc))


def test_unknown_version_rejected():
    """An unsupported report_data_version must fail closed (raise), not silently accept."""
    doc = {
        "nonce": "11" * 32, "fingerprint": "22" * 32, "miner": None,
        "tls_cert_fingerprint": None, "model_hash": None, "usage_chain_head": "",
        "args_hash": None, "gpu_hash": None, "usage_count": None,
        "usage_total_tokens": None, "report_data_version": "v99",
        "report_data_domain_tag": "tee-wrapper/report-data/v99",
        "report_data_fields": [],
    }
    with pytest.raises(UnsupportedReportDataVersion):
        recompute(DocView(doc))
