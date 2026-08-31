"""Regression tests for agent-A's fixes to product_routes.py / mesh.py /
schemas.py / config.py.

A1: an unhosted/unavailable model must never fabricate a billed 200; the dev
    echo fallback must be an explicit opt-in (default OFF, refused in
    production) and self-identifying whenever it does run.
A2: an over-long chat-completions prompt must return a clean 4xx, not an
    unhandled 500.
A3: a fully-drained REAL streaming call must move GET /v1/usage's counters
    exactly once, with the enclave's real token counts; reading /v1/usage
    must not inflate its own "requests" counter.
A4: GET /v1/models must never advertise demo/static ids as real, and must
    keep a registered-but-currently-unavailable model visible (as
    ``status="degraded"``) instead of silently dropping it.
"""

from __future__ import annotations

import uuid

import httpx
from nacl.signing import SigningKey

from inference_api import product_routes
from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel
from inference_api.provisioner.service import ProvisionerService
from inference_api.security import utcnow
from talaris_contracts import AsyncTeeClient

from _tee import StubChatTransport, bind_dev_tee, dev_tee_app


# --------------------------------------------------------------------------- #
# A1 — unknown/unavailable models must not fabricate a billed 200
# --------------------------------------------------------------------------- #


async def test_unregistered_model_returns_404_model_not_found(client, seed_api_key):
    """A model no miner has ever advertised: 404 model_not_found, not a fabricated 200."""
    seeded = await seed_api_key(credits=1_000)

    resp = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "gpt-4o", "prompt": "Say hello"},
    )

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "model_not_found"
    assert "Echo" not in resp.text


async def test_registered_but_unavailable_model_returns_503_no_capacity(
    client, seed_api_key, seed_miner
):
    """A model that IS registered but has no active provisioned token (never
    selectable by MinerSelector) is a different case from "unknown": 503
    no_capacity, distinguishable from the 404 above."""
    seeded = await seed_api_key(credits=1_000)
    await seed_miner(model_ids=["flaky-model"])  # no seed_provisioned_token call

    resp = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "flaky-model", "prompt": "hello"},
    )

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "no_capacity"


async def test_dev_echo_fallback_is_off_by_default_and_self_identifying_when_enabled(
    client, seed_api_key, monkeypatch
):
    assert settings.allow_dev_echo_fallback is False  # safe-by-default setting

    seeded = await seed_api_key(credits=1_000)

    # Default (off): still a clean error, never a fabricated 200.
    off = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "unknown-model", "prompt": "hi"},
    )
    assert off.status_code == 404

    monkeypatch.setattr(settings, "allow_dev_echo_fallback", True)

    on = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "unknown-model", "prompt": "hi"},
    )
    assert on.status_code == 200, on.text
    body = on.json()
    # Machine-readable + human-readable: no client can mistake this for real
    # model output.
    assert body["attested"] is False
    assert body["output"].startswith("[UNATTESTED-DEV-ECHO]")
    # Fabricated output must never be billed.
    assert body["usage"]["charged_tokens"] == 0

    # ... and it must not have inflated the token counters either (it's not
    # real inference work).
    usage = await client.get("/v1/usage", headers={"X-API-Key": seeded.raw_key})
    assert usage.json()["input_tokens"] == 0
    assert usage.json()["output_tokens"] == 0


async def test_openai_chat_completions_echo_fallback_stamps_system_fingerprint(
    client, seed_api_key, monkeypatch
):
    """Same self-identifying contract, via the OpenAI-compatible endpoint: a real
    (nullable) OpenAI field, so compatible clients aren't broken."""
    seeded = await seed_api_key(credits=1_000)
    monkeypatch.setattr(settings, "allow_dev_echo_fallback", True)

    resp = await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "unknown-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["system_fingerprint"] == "dev-echo-unattested"


# --------------------------------------------------------------------------- #
# A2 — an over-long prompt must be a clean 4xx, not an unhandled 500
# --------------------------------------------------------------------------- #


async def test_oversized_chat_completion_prompt_returns_413_not_500(client, seed_api_key):
    seeded = await seed_api_key(credits=100_000)
    too_long = "x" * 20_001

    resp = await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-chat-001", "messages": [{"role": "user", "content": too_long}]},
    )

    assert resp.status_code == 413, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "prompt_too_long"
    assert "20000" in detail["message"]


async def test_inference_endpoint_oversized_prompt_stays_a_clean_422(client, seed_api_key):
    """Locks in the existing-good behavior on /v1/inference (FastAPI binds
    InferenceRequest directly, so pydantic's ValidationError already becomes a
    clean 422 there, not a 500) so it can't silently regress."""
    seeded = await seed_api_key(credits=100_000)
    too_long = "x" * 20_001

    resp = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-chat-001", "prompt": too_long},
    )

    assert resp.status_code == 422, resp.text
    assert "20000" in resp.text


# --------------------------------------------------------------------------- #
# A3 — streaming usage accounting
# --------------------------------------------------------------------------- #

PLATFORM_PROVISIONER_SEED = bytes(range(32))


async def _provision_real_miner(transport, base_url) -> uuid.UUID:
    signing_key = SigningKey.generate()
    hotkey = await bind_dev_tee(transport, base_url, signing_key)
    async with async_session_maker() as session:
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=hotkey,
            tee_endpoint=base_url,
            attestation_status="pending",
            health="healthy",
            last_seen=utcnow(),
            registered_at=utcnow(),
        )
        session.add(miner)
        session.add(
            MinerModel(
                id=uuid.uuid4(),
                miner_id=miner.id,
                model_id="demo-chat-001",
                loaded=True,
                last_advertised_at=utcnow(),
            )
        )
        await session.commit()
        miner_id = miner.id

    def factory(_endpoint):
        return AsyncTeeClient(client=httpx.AsyncClient(base_url=base_url, transport=transport))

    async with async_session_maker() as session:
        service = ProvisionerService(session, tee_client_factory=factory)
        await service.provision_miner(miner_id)
        await session.commit()
    return miner_id


async def test_streaming_inference_moves_usage_counters_exactly_once(client, seed_api_key):
    seeded = await seed_api_key(credits=10_000)
    try:
        async with dev_tee_app() as (transport, base_url):
            await _provision_real_miner(transport, base_url)
            stub = StubChatTransport(transport, prompt_tokens=9, completion_tokens=4)
            product_routes.set_forwarder_transport(stub)

            resp = await client.post(
                "/v1/chat/completions",
                headers={"X-API-Key": seeded.raw_key},
                json={
                    "model": "demo-chat-001",
                    "messages": [{"role": "user", "content": "stream from a miner"}],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )
            assert resp.status_code == 200, resp.text
            # Fully drain the stream, like a real client, so the settlement
            # ``finally`` block in _routed_stream_response has definitely run
            # before we inspect GET /v1/usage below.
            assert "[DONE]" in resp.text
    finally:
        product_routes.set_forwarder_transport(None)

    usage = await client.get("/v1/usage", headers={"X-API-Key": seeded.raw_key})
    assert usage.status_code == 200, usage.text
    body = usage.json()
    # Before the fix: a fully-drained streaming call settled the BILLING
    # ledger but never called APIKeyService.record_usage, so these counters
    # stayed at 0 even though the request was billed correctly.
    assert body["input_tokens"] == 9
    assert body["output_tokens"] == 4
    # Recorded exactly once: not per-SSE-chunk, and not double-counted.
    assert body["requests"] == 1


async def test_reading_usage_does_not_inflate_its_own_requests_counter(
    client, seed_api_key
):
    seeded = await seed_api_key(credits=1_000)

    first = await client.get("/v1/usage", headers={"X-API-Key": seeded.raw_key})
    second = await client.get("/v1/usage", headers={"X-API-Key": seeded.raw_key})
    third = await client.get("/v1/usage", headers={"X-API-Key": seeded.raw_key})

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)
    # Before the fix: GET /v1/usage recorded itself as a usage row, so its own
    # "requests" counter climbed with every read (1, then 2, then 3, ...).
    assert first.json()["requests"] == 0
    assert second.json()["requests"] == 0
    assert third.json()["requests"] == 0


# --------------------------------------------------------------------------- #
# A4 — honest model catalog
# --------------------------------------------------------------------------- #


async def test_empty_registry_returns_empty_catalog_not_demo_models(client, seed_api_key):
    seeded = await seed_api_key(credits=1_000)

    resp = await client.get("/v1/models", headers={"X-API-Key": seeded.raw_key})

    assert resp.status_code == 200, resp.text
    # Before the fix: an empty registry advertised the static demo ids
    # ("demo-inference-001"/"demo-chat-001") as if they were real, hostable
    # models.
    assert resp.json()["data"] == []


async def test_unavailable_registered_model_marked_degraded_not_dropped(
    client, seed_api_key, seed_miner
):
    seeded = await seed_api_key(credits=1_000)
    # Registered (a miner_models row exists) but never selectable (no active
    # provisioned token) -- a real model, not "never registered".
    await seed_miner(model_ids=["flaky-model"])

    resp = await client.get("/v1/models", headers={"X-API-Key": seeded.raw_key})

    assert resp.status_code == 200, resp.text
    by_id = {m["id"]: m for m in resp.json()["data"]}
    # Before the fix: list_available_models() alone backed this endpoint, so an
    # unavailable model disappeared from the catalog entirely instead of being
    # surfaced as degraded.
    assert "flaky-model" in by_id
    assert by_id["flaky-model"]["status"] == "degraded"
    assert by_id["flaky-model"]["owned_by"] == "talaris-miner"


async def test_available_model_listed_and_model_detail_endpoint_agrees(
    client, seed_api_key, seed_miner, seed_provisioned_token
):
    seeded = await seed_api_key(credits=1_000)
    miner = await seed_miner(model_ids=["solid-model"])
    await seed_provisioned_token(miner_id=miner.miner_id)

    listing = await client.get("/v1/models", headers={"X-API-Key": seeded.raw_key})
    by_id = {m["id"]: m for m in listing.json()["data"]}
    assert by_id["solid-model"]["status"] == "available"

    detail = await client.get(
        "/v1/models/solid-model", headers={"X-API-Key": seeded.raw_key}
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "available"

    missing = await client.get(
        "/v1/models/never-registered", headers={"X-API-Key": seeded.raw_key}
    )
    assert missing.status_code == 404
