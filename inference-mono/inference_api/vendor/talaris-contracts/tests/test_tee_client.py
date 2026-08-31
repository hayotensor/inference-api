"""TeeClient / AsyncTeeClient endpoint-shape tests using httpx.MockTransport.

Asserts:
- GET /attestation sends ?nonce=<hex> and parses into AttestationDocument.
- POST /provision body shape (validator_pubkey/sealed/signature) and end-to-end
  seal+sign via build_provision_request.
- chat_completions sends Authorization: Bearer <token> and never logs it.
- POST /bind body shape; GET /stats Bearer header.
"""

from __future__ import annotations

import asyncio
import json
import secrets

import httpx
import pytest
from nacl.public import PrivateKey, SealedBox
from nacl.signing import SigningKey

from talaris_contracts import (
    AsyncTeeClient,
    AttestationDocument,
    BindRequest,
    Credentials,
    TeeClient,
)

# A minimal but structurally-valid attestation document for the mock to return.
_DOC = {
    "mode": "dev",
    "nonce": "00" * 16,
    "report_data": "ab" * 32,
    "public_bundle": {"verify_key": "11" * 32, "sealing_key": "22" * 32},
    "miner": None,
    "evidence": {"dev_signature": "cc" * 64, "warning": "dev"},
    "tls_cert_fingerprint": None,
    "model_hash": None,
    "usage_chain_head": "",
    "args_hash": None,
    "gpu_hash": None,
    "gpu_uuids": [],
    "usage_count": None,
    "usage_total_tokens": None,
    "report_data_version": "v6",
    "report_data_domain_tag": "tee-wrapper/report-data/v6",
    "report_data_fields": ["domain_tag", "nonce"],
    "boot_nonce": "deadbeef",
    "fingerprint": "ff" * 32,
    "tls_pubkey_signature": None,
}


def _make_handler(captured: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["last"] = request
        path = request.url.path
        if path == "/attestation" and request.method == "GET":
            captured["attestation_nonce"] = request.url.params.get("nonce")
            return httpx.Response(200, json=_DOC)
        if path == "/provision" and request.method == "POST":
            captured["provision_body"] = json.loads(request.content)
            return httpx.Response(200, json={"provisioned": True})
        if path == "/bind" and request.method == "POST":
            captured["bind_body"] = json.loads(request.content)
            return httpx.Response(200, json={"bound": True, "miner": "abc"})
        if path == "/v1/chat/completions" and request.method == "POST":
            captured["chat_auth"] = request.headers.get("authorization")
            captured["chat_body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": []})
        if path == "/stats" and request.method == "GET":
            captured["stats_auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"aggregate": {"requests": 0}, "usage_chain_head": ""}
            )
        return httpx.Response(404, json={"detail": "not found"})

    return handler


def test_get_attestation_sends_hex_nonce_and_parses():
    captured: dict = {}
    transport = httpx.MockTransport(_make_handler(captured))
    with TeeClient("http://tee.local", transport=transport) as c:
        nonce = secrets.token_bytes(16)
        doc = c.get_attestation(nonce)
    assert isinstance(doc, AttestationDocument)
    assert captured["attestation_nonce"] == nonce.hex()
    assert doc.boot_nonce == "deadbeef"
    assert doc.public_bundle.sealing_key == "22" * 32


def test_provision_body_shape_end_to_end():
    captured: dict = {}
    transport = httpx.MockTransport(_make_handler(captured))

    enclave_priv = PrivateKey.generate()
    sealing_pub_hex = bytes(enclave_priv.public_key).hex()
    validator = SigningKey.generate()

    with TeeClient("http://tee.local", transport=transport) as c:
        out = c.provision(
            Credentials(admin_token="A", inference_keys={"k1": "I"}),
            sealing_pub_hex,
            validator,
        )
    assert out == {"provisioned": True}
    body = captured["provision_body"]
    assert set(body) >= {"validator_pubkey", "sealed", "signature"}
    assert body["validator_pubkey"] == bytes(validator.verify_key).hex()
    # The sealed ciphertext unseals back to the creds.
    recovered = json.loads(SealedBox(enclave_priv).decrypt(bytes.fromhex(body["sealed"])))
    assert recovered == {"admin_token": "A", "inference_keys": {"k1": "I"}}


def test_chat_completions_sends_bearer_and_does_not_log_token(capsys):
    captured: dict = {}
    transport = httpx.MockTransport(_make_handler(captured))
    token = "super-secret-token-xyz"
    with TeeClient("http://tee.local", transport=transport) as c:
        resp = c.chat_completions(token, {"model": "m", "messages": []})
    assert resp.status_code == 200
    assert captured["chat_auth"] == f"Bearer {token}"
    assert captured["chat_body"]["model"] == "m"
    # The client must not print/log the token anywhere.
    out = capsys.readouterr()
    assert token not in out.out
    assert token not in out.err


def test_bind_body_shape():
    captured: dict = {}
    transport = httpx.MockTransport(_make_handler(captured))
    with TeeClient("http://tee.local", transport=transport) as c:
        out = c.bind(BindRequest(miner_pubkey="aa" * 32, signature="bb" * 64))
    assert out == {"bound": True, "miner": "abc"}
    body = captured["bind_body"]
    assert body["miner_pubkey"] == "aa" * 32
    assert body["signature"] == "bb" * 64
    assert body["key_type"] is None


def test_stats_sends_admin_bearer():
    captured: dict = {}
    transport = httpx.MockTransport(_make_handler(captured))
    with TeeClient("http://tee.local", transport=transport) as c:
        out = c.stats("admin-token-123")
    assert out["aggregate"]["requests"] == 0
    assert captured["stats_auth"] == "Bearer admin-token-123"


def test_injected_client_not_closed_by_wrapper():
    captured: dict = {}
    transport = httpx.MockTransport(_make_handler(captured))
    injected = httpx.Client(base_url="http://tee.local", transport=transport)
    with TeeClient(client=injected) as c:
        c.get_attestation(secrets.token_bytes(8))
    # Wrapper must NOT close an injected client.
    assert not injected.is_closed
    injected.close()


def test_async_client_attestation_and_bearer():
    async def run():
        captured: dict = {}
        transport = httpx.MockTransport(_make_handler(captured))
        async with AsyncTeeClient("http://tee.local", transport=transport) as c:
            nonce = secrets.token_bytes(16)
            doc = await c.get_attestation(nonce)
            resp = await c.chat_completions("tok", {"model": "m", "messages": []})
        assert isinstance(doc, AttestationDocument)
        assert captured["attestation_nonce"] == nonce.hex()
        assert resp.status_code == 200
        assert captured["chat_auth"] == "Bearer tok"

    asyncio.run(run())
