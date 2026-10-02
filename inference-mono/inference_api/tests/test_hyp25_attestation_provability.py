"""HYP-25 / HYP-38 close-out tests.

Covers: the OpenAPI schema no longer default-true's `attested`; `GET /attest`
publishes a documented, fetchable pin; a real inference response carries a
signed, independently-verifiable attestation receipt; and the model-allowlist
trust-on-first-attest sync that lets `GET /allowlist` show a genuinely
observed (non-"demo") model_hash.
"""

import httpx
import pytest

from inference_api import product_routes
from inference_api.allowlist_seed import (
    OBSERVED_ATTESTED_LABEL,
    sync_observed_model_allowlist,
)
from inference_api.attestation import sign_inference_receipt, verify_inference_receipt
from inference_api.db import async_session_maker
from inference_api.main import app
from inference_api.models import ModelAllowlist
from inference_api.provisioner.key import load_provisioner_signing_key, provisioner_verify_key_hex
from inference_api.schemas import InferenceResponse

from _tee import StubChatTransport

_UNREACHABLE_INNER_ERROR_JSON = {"error": "unexpected request"}


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


def test_inference_response_attested_field_has_no_default_true():
    """OpenAPI schema close-out test: `attested` must not default-true.

    Every call site sets it explicitly (see product_routes.py), so making it
    a required field (no default at all) is both accurate and closes the
    HYP-25 finding verbatim: `schema.properties.attested.default is not True`.
    """
    schema = InferenceResponse.model_json_schema()
    attested_schema = schema["properties"]["attested"]
    assert attested_schema.get("default") is not True
    assert "attested" in schema.get("required", [])

    openapi_schema = app.openapi()
    component = openapi_schema["components"]["schemas"]["InferenceResponse"]
    assert component["properties"]["attested"].get("default") is not True
    assert "attested" in component.get("required", [])


async def test_attest_endpoint_publishes_pinned_key_and_recipes(client):
    resp = await client.get("/attest")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platform_pubkey"] == provisioner_verify_key_hex()
    assert body["signing_scheme"] == "ed25519"
    for name in ("model_allowlist", "miner_stats", "inference_receipt"):
        assert name in body["artifacts"]
        assert "kind" in body["artifacts"][name]
        assert "verify" in body["artifacts"][name]


async def test_inference_response_carries_verifiable_attestation_receipt(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """POST /v1/inference: when attested, the body includes a signature a
    third party can verify against the pinned key from GET /attest -- without
    trusting the API's own "attested": true claim."""
    seeded = await seed_api_key(credits=10_000)
    miner = await seed_miner(
        model_ids=["demo-chat-001"],
        model_hash="c" * 64,
        enclave_verify_key="d" * 64,
        tls_cert_fingerprint="e" * 64,
    )
    await seed_provisioned_token(miner_id=miner.miner_id, model_id="demo-chat-001")

    unreachable_inner = httpx.MockTransport(
        lambda request: httpx.Response(500, json=_UNREACHABLE_INNER_ERROR_JSON)
    )
    product_routes.set_forwarder_transport(
        StubChatTransport(
            unreachable_inner,
            content="Hello from the attested miner.",
            prompt_tokens=2,
            completion_tokens=3,
        )
    )

    resp = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-chat-001", "prompt": "hello"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attested"] is True
    attestation = body["attestation"]
    assert attestation is not None
    assert attestation["model_id"] == "demo-chat-001"
    assert attestation["model_hash"] == "c" * 64
    assert attestation["enclave_verify_key"] == "d" * 64
    assert attestation["tls_cert_fingerprint"] == "e" * 64
    assert attestation["signer_pubkey"] == provisioner_verify_key_hex()

    # The pinned key comes from GET /attest, NOT from the receipt's own
    # signer_pubkey field -- exercise exactly that independent path.
    pin = (await client.get("/attest")).json()["platform_pubkey"]
    assert pin == attestation["signer_pubkey"]
    from inference_api.schemas import AttestationReceipt

    receipt = AttestationReceipt.model_validate(attestation)
    assert verify_inference_receipt(receipt, pin) is True

    # Tampering with any signed field must break verification.
    tampered = receipt.model_copy(update={"model_hash": "f" * 64})
    assert verify_inference_receipt(tampered, pin) is False


async def test_dev_echo_fallback_never_carries_an_attestation_receipt(
    client, seed_api_key, monkeypatch
):
    from inference_api.config import settings

    monkeypatch.setattr(settings, "allow_dev_echo_fallback", True)
    seeded = await seed_api_key(credits=10_000)
    resp = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "no-such-model", "prompt": "hello"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attested"] is False
    assert body["attestation"] is None


def test_sign_and_verify_inference_receipt_roundtrip():
    key = load_provisioner_signing_key()
    receipt = sign_inference_receipt(
        request_id="req-1",
        model_id="demo-chat-001",
        model_hash="a" * 64,
        enclave_verify_key="b" * 64,
        tls_cert_fingerprint="c" * 64,
        miner_hash="d" * 64,
        attestation_mode="nvidia",
        attestation_verified_at=1_700_000_000,
        signing_key=key,
    )
    verify_key_hex = bytes(key.verify_key).hex()
    assert verify_inference_receipt(receipt, verify_key_hex) is True
    assert verify_inference_receipt(receipt, "0" * 64) is False


async def test_sync_observed_model_allowlist_publishes_real_hash_once():
    """Trust-on-first-attest: a genuinely observed, hardware-verified
    model_hash gets published (label != 'demo') the first time a model has no
    active pin, and is never duplicated or overridden afterward."""
    async with async_session_maker() as session:
        await sync_observed_model_allowlist(
            session,
            model_id="prod-model-1",
            model_version="v1",
            model_hash="a" * 64,
        )
        await session.commit()

    async with async_session_maker() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(ModelAllowlist).where(ModelAllowlist.model_id == "prod-model-1")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].model_hash == "a" * 64
        assert rows[0].label == OBSERVED_ATTESTED_LABEL
        assert rows[0].active is True

    # A second attestation with a DIFFERENT hash must NOT add a second pin --
    # an existing active entry already anchors trust for this model_id.
    async with async_session_maker() as session:
        await sync_observed_model_allowlist(
            session,
            model_id="prod-model-1",
            model_version="v1",
            model_hash="b" * 64,
        )
        await session.commit()

    async with async_session_maker() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(ModelAllowlist).where(ModelAllowlist.model_id == "prod-model-1")
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].model_hash == "a" * 64


async def test_sync_observed_model_allowlist_ignores_placeholder_hashes():
    async with async_session_maker() as session:
        await sync_observed_model_allowlist(
            session, model_id="unstarted-model", model_version=None, model_hash=None
        )
        await sync_observed_model_allowlist(
            session, model_id="unstarted-model", model_version=None, model_hash="0" * 64
        )
        await session.commit()

    async with async_session_maker() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(
                select(ModelAllowlist).where(ModelAllowlist.model_id == "unstarted-model")
            )
        ).scalars().all()
        assert rows == []


async def test_allowlist_can_show_a_real_non_demo_non_zero_model_hash(client):
    """End-to-end close-out check: once a model has been genuinely attested,
    GET /allowlist serves a non-demo entry whose model_hash is not all zeros."""
    async with async_session_maker() as session:
        await sync_observed_model_allowlist(
            session,
            model_id="prod-model-2",
            model_version="v1",
            model_hash="9" * 64,
        )
        await session.commit()

    resp = await client.get("/allowlist")
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    non_demo = [e for e in entries if e.get("label") != "demo"]
    assert non_demo, "expected at least one non-demo allowlist entry"
    assert all(e["model_hash"] != "0" * 64 for e in non_demo)
    assert any(e["model_id"] == "prod-model-2" and e["model_hash"] == "9" * 64 for e in non_demo)
