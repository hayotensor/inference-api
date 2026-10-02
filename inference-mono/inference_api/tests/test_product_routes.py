import json
from datetime import timedelta

import httpx
import pytest

from inference_api import product_routes
from inference_api.db import async_session_maker
from inference_api.models import APIKey
from inference_api.security import utcnow

from _tee import StubChatTransport

# The forwarder only ever calls this on the injected transport when it has no
# real "other traffic" to fall through to (these tests seed the miner/token
# rows directly via the conftest fixtures, bypassing attestation/provisioning
# entirely, so nothing here should ever hit a path other than
# POST /v1/chat/completions). Any request that isn't that is a test bug.
_UNREACHABLE_INNER = httpx.MockTransport(
    lambda request: httpx.Response(500, json={"error": f"unexpected request to {request.url}"})
)


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


async def _seed_attested_miner(seed_miner, seed_provisioned_token, *, model_id: str):
    """Stand up a fully attested + healthy + tokened miner serving ``model_id``.

    Uses the same conftest fixtures (``seed_miner`` / ``seed_provisioned_token``)
    that ``test_routing_forward.py`` and the other fix-round regression tests use
    to get a genuinely eligible ``MinerSelector`` candidate, without needing the
    real dev-TEE producer app (these tests are about bearer auth / usage
    accounting / SSE streaming, not attestation, so only the enclave's HTTP
    response -- faked via ``StubChatTransport`` -- needs to be real).
    """
    miner = await seed_miner(model_ids=[model_id])
    await seed_provisioned_token(miner_id=miner.miner_id, model_id=model_id)
    return miner


def _stub_transport(*, content: str, prompt_tokens: int, completion_tokens: int) -> StubChatTransport:
    return StubChatTransport(
        _UNREACHABLE_INNER,
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def test_models_inference_and_usage_summary(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    seeded = await seed_api_key(credits=10_000)
    # A real attested/healthy/tokened miner serving the model /v1/inference
    # defaults to, so the registry is honest (no invented demo ids) AND the
    # inference call below exercises the genuine reserve -> forward -> settle
    # path instead of the removed echo fallback.
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")

    models = await client.get("/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"})
    assert models.status_code == 200, models.text
    assert [model["id"] for model in models.json()["data"]] == ["demo-inference-001"]
    assert models.json()["data"][0]["status"] == "available"

    async with async_session_maker() as session:
        db_key = await session.get(APIKey, seeded.api_key_id)
        assert db_key.last_used_at is not None

    product_routes.set_forwarder_transport(
        _stub_transport(content="Hello from the attested miner.", prompt_tokens=2, completion_tokens=3)
    )
    inference = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"prompt": "hello platform"},
    )
    assert inference.status_code == 200, inference.text
    body = inference.json()
    # Real, attested output -- not a fabricated echo.
    assert body["output"] == "Hello from the attested miner."
    assert body["attested"] is True
    assert body["usage"]["input_tokens"] == 2
    assert body["usage"]["output_tokens"] == 3
    assert body["usage"]["remaining_tokens"] == 9_995

    usage = await client.get("/v1/usage", headers={"Authorization": f"Bearer {seeded.raw_key}"})
    assert usage.status_code == 200, usage.text
    assert usage.json()["api_key_id"] == str(seeded.api_key_id)
    # /v1/models + /v1/inference recorded a request each; GET /v1/usage itself
    # deliberately does not increment its own counter (see get_usage).
    assert usage.json()["requests"] >= 2
    assert usage.json()["input_tokens"] == 2
    assert usage.json()["output_tokens"] == 3


async def test_inference_response_id_is_unique_per_request_not_the_api_key(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """Regression test for HYP-40.

    `id` used to be `f"inf_{db_key.id.hex[:24]}"` -- a function of the API key,
    so every call made with the same key returned the identical `id`. It must
    instead be unique per request (the same id used for stats/attestation) and
    match `attestation.request_id` when the response is attested.
    """
    seeded = await seed_api_key(credits=10_000)
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(
        _stub_transport(content="hi", prompt_tokens=1, completion_tokens=1)
    )

    first = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key, "X-Request-ID": "req-one"},
        json={"prompt": "hello platform"},
    )
    second = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key, "X-Request-ID": "req-two"},
        json={"prompt": "hello platform again"},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body, second_body = first.json(), second.json()

    api_key_derived_id = f"inf_{seeded.api_key_id.hex[:24]}"
    assert first_body["id"] != api_key_derived_id
    assert second_body["id"] != api_key_derived_id
    assert first_body["id"] != second_body["id"]
    assert first_body["id"] == "inf_req-one"
    assert second_body["id"] == "inf_req-two"
    # Matches the signed attestation receipt's request_id when attested.
    assert first_body["attested"] is True
    assert first_body["id"] == f"inf_{first_body['attestation']['request_id']}"
    assert second_body["id"] == f"inf_{second_body['attestation']['request_id']}"


async def test_api_key_scope_expiration_and_revocation_failures(client, seed_api_key):
    wildcard = await seed_api_key(raw_key="sk_test_wildcard", scopes=["*"])
    wildcard_response = await client.get("/v1/models", headers={"Authorization": f"Bearer {wildcard.raw_key}"})
    assert wildcard_response.status_code == 403

    scoped = await seed_api_key(raw_key="sk_test_models_only", scopes=["models:read"], credits=10_000)
    allowed = await client.get("/v1/models", headers={"Authorization": f"Bearer {scoped.raw_key}"})
    assert allowed.status_code == 200
    forbidden = await client.post(
        "/v1/inference",
        headers={"Authorization": f"Bearer {scoped.raw_key}"},
        json={"prompt": "hello"},
    )
    assert forbidden.status_code == 403

    expired = await seed_api_key(raw_key="sk_test_expired", expires_at=utcnow() - timedelta(minutes=1))
    expired_response = await client.get("/v1/models", headers={"Authorization": f"Bearer {expired.raw_key}"})
    assert expired_response.status_code == 401

    revoked = await seed_api_key(raw_key="sk_test_revoked", revoked_at=utcnow())
    revoked_response = await client.get("/v1/models", headers={"Authorization": f"Bearer {revoked.raw_key}"})
    assert revoked_response.status_code == 401


async def test_openai_chat_completion_uses_bearer_key_and_tracks_usage(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """Bearer-token API-key auth still works, and usage still gets tracked --
    now proven against a REAL attested miner rather than the removed echo
    fallback that used to make this convenient."""
    seeded = await seed_api_key(raw_key="sk_test_openai_chat", credits=10_000)
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-chat-001")
    product_routes.set_forwarder_transport(
        _stub_transport(content="Hello from the attested miner.", prompt_tokens=6, completion_tokens=7)
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {seeded.raw_key}"},
        json={
            "model": "demo-chat-001",
            "messages": [
                {"role": "system", "content": "Answer tersely."},
                {"role": "user", "content": "hello platform"},
            ],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("chatcmpl_")
    assert body["object"] == "chat.completion"
    assert body["model"] == "demo-chat-001"
    assert body["choices"] == [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello from the attested miner.",
            },
            "finish_reason": "stop",
        }
    ]
    # null system_fingerprint == real attested completion, not the dev echo
    # fallback (which stamps "dev-echo-unattested").
    assert body["system_fingerprint"] is None
    assert body["usage"] == {
        "prompt_tokens": 6,
        "completion_tokens": 7,
        "total_tokens": 13,
    }

    usage = await client.get("/v1/usage", headers={"Authorization": f"Bearer {seeded.raw_key}"})
    assert usage.status_code == 200, usage.text
    assert usage.json()["input_tokens"] == 6
    assert usage.json()["output_tokens"] == 7


async def test_openai_chat_completion_streams_sse_chunks(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """Streaming still produces real SSE chunks terminated by [DONE], with a
    final usage chunk carrying the REAL token counts -- now via the genuine
    routed streaming pass-through (product_routes._routed_stream_response)
    against an attested miner, since that path relays the miner's raw SSE
    bytes unmodified rather than the platform's own synthetic per-token
    framing the old (fallback-only) chat_completion_stream generator used.
    """
    seeded = await seed_api_key(raw_key="sk_test_openai_stream", credits=10_000)
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-chat-001")
    product_routes.set_forwarder_transport(
        _stub_transport(content="Hello from the attested miner.", prompt_tokens=3, completion_tokens=4)
    )

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {seeded.raw_key}"},
        json={
            "model": "demo-chat-001",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "stream hello"}]}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]

    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert any(
        chunk["choices"] and chunk["choices"][0].get("delta") == {"content": "Hello from the attested miner."}
        for chunk in chunks
    )
    assert any(
        chunk["choices"] and chunk["choices"][0].get("finish_reason") == "stop"
        for chunk in chunks
    )
    usage_chunks = [chunk for chunk in chunks if chunk.get("choices") == [] and chunk.get("usage")]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 4,
        "total_tokens": 7,
    }


async def test_openai_chat_completion_requires_inference_scope(client, seed_api_key):
    seeded = await seed_api_key(raw_key="sk_test_no_inference_scope", scopes=["models:read"], credits=10_000)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {seeded.raw_key}"},
        json={
            "model": "demo-chat-001",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 403
