"""HYP-45: API key model scoping (serving plane / hot path).

An API key's optional ``allowed_models`` allowlist (mirrored from
``app/db/models/api_key.py`` -- see ``inference_api/models.py``) restricts
which models it can be used against. ``None``/empty means unrestricted (every
key created before this existed, and the default for new keys unless the
dashboard user opts in), matching how ``scopes`` already behaves.

Enforced in ``product_routes._check_model_allowed``, called from
``execute_inference`` (covers ``POST /v1/inference``), from
``create_chat_completion`` before both the streaming and non-streaming
branches (covers ``POST /v1/chat/completions``), and reflected in the catalog
itself (``list_models`` filters, ``get_model`` 404s) so a scoped key sees an
out-of-scope model as simply absent rather than merely forbidden on the
inference routes.
"""

from __future__ import annotations

import httpx
import pytest

from inference_api import product_routes

from _tee import StubChatTransport

_UNREACHABLE_INNER_MESSAGE = "unexpected request -- test should not reach the transport for a blocked model"


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


async def _seed_attested_miner(seed_miner, seed_provisioned_token, *, model_id: str):
    miner = await seed_miner(model_ids=[model_id])
    await seed_provisioned_token(miner_id=miner.miner_id, model_id=model_id)
    return miner


def _stub_transport(*, content: str = "ok", prompt_tokens: int = 1, completion_tokens: int = 1):
    unreachable = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"error": _UNREACHABLE_INNER_MESSAGE})
    )
    return StubChatTransport(
        unreachable, content=content, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )


async def test_unrestricted_key_reaches_every_model(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """The default (allowed_models=None) must keep working exactly as before --
    this is the backward-compatibility guarantee for every key that existed
    before HYP-45."""
    seeded = await seed_api_key(credits=10_000, allowed_models=None)
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(_stub_transport())

    response = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-inference-001", "prompt": "hello"},
    )
    assert response.status_code == 200, response.text


async def test_scoped_key_can_use_an_allowed_model(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    seeded = await seed_api_key(credits=10_000, allowed_models=["demo-inference-001"])
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(_stub_transport())

    response = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-inference-001", "prompt": "hello"},
    )
    assert response.status_code == 200, response.text


async def test_scoped_key_is_forbidden_from_an_unlisted_model(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """A key scoped to one model must not be able to reach another, even though
    it is fully attested/healthy/tokened -- the allowlist check must run before
    the request ever reaches the miner selector/forwarder."""
    seeded = await seed_api_key(credits=10_000, allowed_models=["demo-chat-001"])
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(_stub_transport())

    response = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-inference-001", "prompt": "hello"},
    )
    assert response.status_code == 403, response.text


async def test_scoped_key_chat_completions_enforced_non_streaming(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    seeded = await seed_api_key(credits=10_000, allowed_models=["demo-chat-001"])
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(_stub_transport())

    response = await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": seeded.raw_key},
        json={"model": "demo-inference-001", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 403, response.text


async def test_scoped_key_chat_completions_enforced_streaming(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """The streaming branch of create_chat_completion calls MinerSelector
    directly and never reaches execute_inference's own check -- this pins the
    dedicated `_check_model_allowed` call product_routes makes up front for
    exactly that reason."""
    seeded = await seed_api_key(credits=10_000, allowed_models=["demo-chat-001"])
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(_stub_transport())

    response = await client.post(
        "/v1/chat/completions",
        headers={"X-API-Key": seeded.raw_key},
        json={
            "model": "demo-inference-001",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 403, response.text


async def test_scoped_key_model_catalog_is_filtered(
    client, seed_api_key, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """list_models/get_model must agree with the enforcement on the inference
    routes: an out-of-scope model is absent from the catalog, not merely
    forbidden to call."""
    seeded = await seed_api_key(credits=10_000, allowed_models=["demo-chat-001"])
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-chat-001")

    models = await client.get("/v1/models", headers={"X-API-Key": seeded.raw_key})
    assert models.status_code == 200, models.text
    assert [m["id"] for m in models.json()["data"]] == ["demo-chat-001"]

    allowed_detail = await client.get("/v1/models/demo-chat-001", headers={"X-API-Key": seeded.raw_key})
    assert allowed_detail.status_code == 200, allowed_detail.text

    blocked_detail = await client.get("/v1/models/demo-inference-001", headers={"X-API-Key": seeded.raw_key})
    assert blocked_detail.status_code == 404, blocked_detail.text
