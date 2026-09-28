"""HYP-67: anonymous "sandbox" inference (``POST /v1/guest/inference``).

Covers the three things the ticket calls out explicitly:

* BOTH daily ceilings (per-``X-Guest-Id`` and per-IP) are enforced
  independently and each can trip a 429 on its own.
* The model and ``max_tokens`` are forced server-side regardless of what the
  request body asks for.
* The counters reset once the UTC-day window rolls over (exercised by
  injecting ``now`` directly into ``guest_limits.check_and_increment_guest_limits``
  rather than sleeping or manipulating wall-clock time).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

import inference_api.redis as redis_module
from inference_api.config import settings
from inference_api.guest_limits import (
    CLIENT_LIMIT_EXCEEDED,
    IP_LIMIT_EXCEEDED,
    check_and_increment_guest_limits,
)

from _tee import StubChatTransport


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_per_test():
    """Isolate each test's counters and dodge the cross-test-event-loop redis
    client artifact -- see the identical fixture in test_fix_c_miner_authz.py.
    """
    if redis_module._redis_client is not None:
        try:
            await redis_module._redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        redis_module._redis_client = None
    client = redis_module.get_redis_client()
    await client.flushdb()
    yield
    await client.flushdb()


_UNREACHABLE_INNER = httpx.MockTransport(
    lambda request: httpx.Response(500, json={"error": f"unexpected request to {request.url}"})
)


class _CapturingStubTransport(StubChatTransport):
    """Same fake enclave as the product-routes tests, but remembers every body
    actually sent to /v1/chat/completions so a test can assert on exactly what
    was forwarded (not just what the client asked for)."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_bodies: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            self.sent_bodies.append(json.loads(request.content or b"{}"))
        return await super().handle_async_request(request)


@pytest.fixture
def reset_forwarder_transport():
    from inference_api import product_routes

    yield
    product_routes.set_forwarder_transport(None)


async def _seed_guest_miner(seed_miner, seed_provisioned_token, *, model_id: str = "demo-chat-001"):
    miner = await seed_miner(model_ids=[model_id])
    await seed_provisioned_token(miner_id=miner.miner_id, model_id=model_id)
    return miner


async def test_guest_inference_forces_model_and_max_tokens(
    client, seed_miner, seed_provisioned_token, reset_forwarder_transport
):
    """The cheapest active model_pricing entry ("demo-chat-001", tied at
    multiplier 1 with "demo-inference-001"/"small-model" but alphabetically
    first) and settings.guest_max_tokens are forwarded to the enclave no
    matter what the caller's body asks for.
    """
    await _seed_guest_miner(seed_miner, seed_provisioned_token, model_id="demo-chat-001")
    from inference_api import product_routes

    stub = _CapturingStubTransport(_UNREACHABLE_INNER, content="hi from the sandbox", prompt_tokens=3, completion_tokens=4)
    product_routes.set_forwarder_transport(stub)

    response = await client.post(
        "/v1/guest/inference",
        headers={"X-Guest-Id": str(uuid.uuid4())},
        json={"prompt": "hello sandbox", "model": "large-model", "max_tokens": 999_999},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model"] == "demo-chat-001"
    assert body["output"] == "hi from the sandbox"
    assert body["attested"] is True
    assert body["remaining_today"] == settings.guest_client_daily_limit - 1

    assert len(stub.sent_bodies) == 1
    forwarded = stub.sent_bodies[0]
    # Forced regardless of the client's "large-model" / 999999 in the body.
    assert forwarded["model"] == "demo-chat-001"
    assert forwarded["max_tokens"] == settings.guest_max_tokens


async def test_guest_inference_requires_guest_id_header(client):
    response = await client.post("/v1/guest/inference", json={"prompt": "hello"})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "guest_id_required"


async def test_guest_inference_client_daily_limit_enforced(
    client, seed_miner, seed_provisioned_token, reset_forwarder_transport, monkeypatch
):
    monkeypatch.setattr(settings, "guest_client_daily_limit", 2)
    monkeypatch.setattr(settings, "guest_ip_daily_limit", 1000)
    await _seed_guest_miner(seed_miner, seed_provisioned_token, model_id="demo-chat-001")
    from inference_api import product_routes

    product_routes.set_forwarder_transport(
        StubChatTransport(_UNREACHABLE_INNER, content="ok", prompt_tokens=1, completion_tokens=1)
    )
    guest_id = str(uuid.uuid4())
    headers = {"X-Guest-Id": guest_id}

    first = await client.post("/v1/guest/inference", headers=headers, json={"prompt": "one"})
    second = await client.post("/v1/guest/inference", headers=headers, json={"prompt": "two"})
    third = await client.post("/v1/guest/inference", headers=headers, json={"prompt": "three"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429, third.text
    assert third.json()["detail"]["code"] == CLIENT_LIMIT_EXCEEDED


async def test_guest_inference_ip_daily_limit_enforced(
    client, seed_miner, seed_provisioned_token, reset_forwarder_transport, monkeypatch
):
    """Different browsers (different X-Guest-Id each time) behind the SAME IP
    still trip the shared per-IP ceiling -- the httpx ASGI test transport
    always reports the same fixed peer address, which stands in here for
    "one real-world IP, many client_ids".
    """
    monkeypatch.setattr(settings, "guest_client_daily_limit", 1000)
    monkeypatch.setattr(settings, "guest_ip_daily_limit", 2)
    await _seed_guest_miner(seed_miner, seed_provisioned_token, model_id="demo-chat-001")
    from inference_api import product_routes

    product_routes.set_forwarder_transport(
        StubChatTransport(_UNREACHABLE_INNER, content="ok", prompt_tokens=1, completion_tokens=1)
    )

    first = await client.post(
        "/v1/guest/inference", headers={"X-Guest-Id": str(uuid.uuid4())}, json={"prompt": "one"}
    )
    second = await client.post(
        "/v1/guest/inference", headers={"X-Guest-Id": str(uuid.uuid4())}, json={"prompt": "two"}
    )
    third = await client.post(
        "/v1/guest/inference", headers={"X-Guest-Id": str(uuid.uuid4())}, json={"prompt": "three"}
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429, third.text
    assert third.json()["detail"]["code"] == IP_LIMIT_EXCEEDED


async def test_guest_limits_reset_after_24h_window():
    """Direct unit test of the Redis counter logic: injects ``now`` across a
    UTC-day boundary instead of sleeping or fiddling with key TTLs.
    """
    redis_client = redis_module.get_redis_client()
    client_id = f"reset-test-{uuid.uuid4()}"
    ip = "203.0.113.9"
    just_before_midnight = datetime(2026, 1, 1, 23, 59, 30, tzinfo=UTC)
    just_after_midnight = datetime(2026, 1, 2, 0, 0, 5, tzinfo=UTC)

    for _ in range(3):
        result = await check_and_increment_guest_limits(
            redis_client,
            client_id=client_id,
            ip=ip,
            client_limit=3,
            ip_limit=3,
            now=just_before_midnight,
        )
        assert result.allowed, result

    exhausted = await check_and_increment_guest_limits(
        redis_client,
        client_id=client_id,
        ip=ip,
        client_limit=3,
        ip_limit=3,
        now=just_before_midnight,
    )
    assert exhausted.allowed is False
    assert exhausted.reason == CLIENT_LIMIT_EXCEEDED

    # A request timestamped the next UTC day lands in a fresh bucket even
    # though it is only a few seconds later on the wall clock.
    reset = await check_and_increment_guest_limits(
        redis_client,
        client_id=client_id,
        ip=ip,
        client_limit=3,
        ip_limit=3,
        now=just_after_midnight,
    )
    assert reset.allowed is True
    assert reset.client_count == 1
    assert reset.ip_count == 1
