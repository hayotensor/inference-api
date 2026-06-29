"""Routing + forwarding tests.

Covers:
  * MinerSelector eligibility + least-loaded ranking + failover ordering;
  * a full reserve -> forward -> settle through /v1/chat/completions against a
    REAL provisioned dev-TEE miner (chat endpoint stubbed since the dev TEE has
    no engine) — asserting the InferenceUsageEvent carries miner_id and the REAL
    token counts the enclave reported (NOT len(text.split()));
  * streaming settlement (usage captured from the SSE stream).

We exercise the genuine attestation + provision path (ProvisionerService against
the in-process dev TEE) and only stub the chat-completions response, because the
dev TEE has no inference engine. The reserve/forward/settle accounting is fully
real.
"""

import uuid

import httpx
import pytest
from nacl.signing import SigningKey
from sqlalchemy import select

from talaris_contracts import AsyncTeeClient

from inference_api import product_routes
from inference_api.db import async_session_maker
from inference_api.models import InferenceUsageEvent, Miner, MinerModel, ProvisionedToken
from inference_api.provisioner.service import ProvisionerService
from inference_api.routing.selector import MinerSelector
from inference_api.security import utcnow

from _tee import StubChatTransport, bind_dev_tee, dev_tee_app


# --------------------------------------------------------------------------- #
# Selector unit tests (no TEE needed)
# --------------------------------------------------------------------------- #


async def _seed_attested_miner(
    session,
    *,
    hotkey: str,
    model_id: str = "demo-chat-001",
    available: int = 1,
    queue: int = 0,
    health: str = "healthy",
    attested: bool = True,
    with_token: bool = True,
) -> uuid.UUID:
    miner = Miner(
        id=uuid.uuid4(),
        hotkey=hotkey,
        tee_endpoint=f"http://{hotkey}.test",
        attestation_status="attested" if attested else "pending",
        attestation_mode="dev",
        health=health,
        tls_cert_fingerprint="cc" * 32,
        enclave_verify_key="dd" * 32,
        capacity={"available_concurrent_requests": available, "queue_depth": queue},
        last_seen=utcnow(),
        registered_at=utcnow(),
    )
    session.add(miner)
    session.add(
        MinerModel(
            id=uuid.uuid4(),
            miner_id=miner.id,
            model_id=model_id,
            attestation_status="attested" if attested else "pending",
            loaded=True,
            last_advertised_at=utcnow(),
        )
    )
    if with_token:
        from inference_api.crypto import encrypt_token

        session.add(
            ProvisionedToken(
                id=uuid.uuid4(),
                miner_id=miner.id,
                model_id=model_id,
                key_id="platform-default",
                encrypted_token=encrypt_token(f"inf_{hotkey}"),
                status="active",
                provisioned_at=utcnow(),
            )
        )
    await session.flush()
    return miner.id


async def test_selector_ranks_least_loaded_and_filters_ineligible():
    async with async_session_maker() as session:
        busy = await _seed_attested_miner(session, hotkey="aa" * 32, available=1, queue=5)
        idle = await _seed_attested_miner(session, hotkey="bb" * 32, available=8, queue=0)
        # Ineligible: not attested.
        await _seed_attested_miner(session, hotkey="cc" * 32, attested=False)
        # Ineligible: unhealthy.
        await _seed_attested_miner(session, hotkey="dd" * 32, health="unhealthy")
        # Ineligible: no active token.
        await _seed_attested_miner(session, hotkey="ee" * 32, with_token=False)
        await session.commit()

        candidates = await MinerSelector(session).select("demo-chat-001")
        ids = [c.miner_id for c in candidates]
        assert set(ids) == {busy, idle}
        # Least-loaded (more available capacity) ranks first.
        assert candidates[0].miner_id == idle


async def test_selector_returns_empty_for_unknown_model():
    async with async_session_maker() as session:
        await _seed_attested_miner(session, hotkey="11" * 32, model_id="demo-chat-001")
        await session.commit()
        assert await MinerSelector(session).select("not-hosted") == []


# --------------------------------------------------------------------------- #
# End-to-end reserve -> forward -> settle against a REAL provisioned dev TEE
# --------------------------------------------------------------------------- #


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


async def _provision_real_miner(transport, base_url) -> tuple[uuid.UUID, str]:
    """Bind the dev enclave to a fresh hotkey, register + provision it. Returns
    (miner_id, hotkey)."""
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
        # The provisioner now stamps per-model attestation state and mints the
        # per-(miner, model) token (model_id set) itself, so the per-model selector
        # gate and the per-model token join resolve this enclave with no manual fixup.
        await service.provision_miner(miner_id)
        await session.commit()
    return miner_id, hotkey


async def test_chat_completion_forwards_and_settles_real_counts(
    client, seed_api_key, reset_forwarder_transport
):
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)

        # Forward chat via a stub-chat transport over the same real dev TEE.
        stub = StubChatTransport(transport, prompt_tokens=11, completion_tokens=6)
        product_routes.set_forwarder_transport(stub)

        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded.raw_key}"},
            json={
                "model": "demo-chat-001",
                "messages": [{"role": "user", "content": "route me to a miner"}],
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "Hello from the stubbed enclave engine."
    # REAL counts from the enclave usage block, not a word-count estimate.
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17}

    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(InferenceUsageEvent).where(InferenceUsageEvent.status == "settled")
            )
        ).scalar_one()
        assert event.miner_id == miner_id
        assert event.miner_hotkey == hotkey
        assert event.prompt_tokens == 11
        assert event.completion_tokens == 6


async def test_chat_completion_streams_and_settles_real_counts(
    client, seed_api_key, reset_forwarder_transport
):
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        miner_id, _hotkey = await _provision_real_miner(transport, base_url)
        stub = StubChatTransport(transport, prompt_tokens=9, completion_tokens=4)
        product_routes.set_forwarder_transport(stub)

        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded.raw_key}"},
            json={
                "model": "demo-chat-001",
                "messages": [{"role": "user", "content": "stream from a miner"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = resp.text
        assert "Hello from the stubbed enclave engine." in text
        assert "[DONE]" in text

    # Settlement happens in the streaming finally — verify it landed with real counts.
    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(InferenceUsageEvent).where(InferenceUsageEvent.status == "settled")
            )
        ).scalar_one()
        assert event.miner_id == miner_id
        assert event.prompt_tokens == 9
        assert event.completion_tokens == 4


async def test_forward_failover_to_second_candidate(
    client, seed_api_key, reset_forwarder_transport
):
    """First candidate's token errors (401 twice) -> failover to the second."""
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        good_id, _hotkey = await _provision_real_miner(transport, base_url)

        # A second miner that is attested+healthy+tokened but whose endpoint is
        # unreachable, ranked FIRST by giving it more available capacity.
        async with async_session_maker() as session:
            from inference_api.crypto import encrypt_token

            bad = Miner(
                id=uuid.uuid4(),
                hotkey="66" * 32,
                tee_endpoint="http://unreachable.miner",
                attestation_status="attested",
                attestation_mode="dev",
                health="healthy",
                tls_cert_fingerprint="ee" * 32,
                enclave_verify_key="ff" * 32,
                capacity={"available_concurrent_requests": 99, "queue_depth": 0},
                last_seen=utcnow(),
                registered_at=utcnow(),
            )
            session.add(bad)
            session.add(
                MinerModel(
                    id=uuid.uuid4(), miner_id=bad.id, model_id="demo-chat-001",
                    attestation_status="attested",
                    loaded=True, last_advertised_at=utcnow(),
                )
            )
            session.add(
                ProvisionedToken(
                    id=uuid.uuid4(), miner_id=bad.id, model_id="demo-chat-001", key_id="k",
                    encrypted_token=encrypt_token("inf_bad"), status="active",
                    provisioned_at=utcnow(),
                )
            )
            await session.commit()

        # A transport that 500s for the unreachable miner's host, else stub-chats.
        class FailoverTransport(StubChatTransport):
            async def handle_async_request(self, request):
                if request.url.host == "unreachable.miner":
                    raise httpx.ConnectError("connection refused")
                return await super().handle_async_request(request)

        product_routes.set_forwarder_transport(
            FailoverTransport(transport, prompt_tokens=8, completion_tokens=3)
        )

        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded.raw_key}"},
            json={
                "model": "demo-chat-001",
                "messages": [{"role": "user", "content": "failover please"}],
            },
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["usage"]["total_tokens"] == 11

    async with async_session_maker() as session:
        event = (
            await session.execute(
                select(InferenceUsageEvent).where(InferenceUsageEvent.status == "settled")
            )
        ).scalar_one()
        # Settled against the reachable (second) miner, reusing the SAME reservation.
        assert event.miner_id == good_id
