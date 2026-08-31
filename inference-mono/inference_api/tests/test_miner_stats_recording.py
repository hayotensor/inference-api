"""Per-attempt miner statistics recording.

The point of these tests is the asymmetry the old merkle-DAG design could not fix:
a miner that accepted routing and then failed must be recorded, not silently
skipped. They drive the REAL request path (reserve -> forward -> settle) against a
real provisioned dev TEE and assert on the ``miner_request_stats`` rows it leaves
behind.
"""

import uuid

import httpx
import pytest
from sqlalchemy import select

from inference_api import product_routes, stats_recorder
from inference_api.chain import MockChainClient
from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel, MinerRequestStat, ProvisionedToken
from inference_api.routing.forwarder import (
    OUTCOME_FORWARD_ERROR,
    OUTCOME_HTTP_ERROR,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
    ForwardError,
    NoActiveTokenError,
)
from inference_api.security import utcnow

from _tee import StubChatTransport, dev_tee_app
from test_routing_forward import _provision_real_miner


TEST_EPOCH = 41


@pytest.fixture
def stub_epoch():
    """Pin the stamped epoch so assertions do not depend on a live chain."""
    stats_recorder.set_chain_client(MockChainClient(allow_unknown=True, epoch=TEST_EPOCH))
    yield TEST_EPOCH
    stats_recorder.set_chain_client(None)


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


async def _stats_rows() -> list[MinerRequestStat]:
    async with async_session_maker() as session:
        result = await session.execute(
            select(MinerRequestStat).order_by(MinerRequestStat.created_at)
        )
        return list(result.scalars().all())


async def _seed_unreachable_miner(host: str, *, hotkey: str, rank_first: bool = True) -> uuid.UUID:
    """An attested+healthy+tokened miner whose endpoint cannot be reached."""
    from inference_api.crypto import encrypt_token

    async with async_session_maker() as session:
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=hotkey,
            tee_endpoint=f"http://{host}",
            attestation_status="attested",
            attestation_mode="dev",
            health="healthy",
            tls_cert_fingerprint="ee" * 32,
            enclave_verify_key="ff" * 32,
            # More available capacity ranks it FIRST, so it is always attempted.
            capacity={"available_concurrent_requests": 99 if rank_first else 1, "queue_depth": 0},
            last_seen=utcnow(),
            registered_at=utcnow(),
            subnet_node_id=7,
        )
        session.add(miner)
        session.add(
            MinerModel(
                id=uuid.uuid4(),
                miner_id=miner.id,
                model_id="demo-chat-001",
                attestation_status="attested",
                loaded=True,
                last_advertised_at=utcnow(),
            )
        )
        session.add(
            ProvisionedToken(
                id=uuid.uuid4(),
                miner_id=miner.id,
                model_id="demo-chat-001",
                key_id="k",
                encrypted_token=encrypt_token("inf_bad"),
                status="active",
                provisioned_at=utcnow(),
            )
        )
        await session.commit()
        return miner.id


# --------------------------------------------------------------------------- #
# Success path
# --------------------------------------------------------------------------- #


async def test_success_records_attempt_with_settled_counts_and_latency(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)
        product_routes.set_forwarder_transport(
            StubChatTransport(transport, prompt_tokens=11, completion_tokens=6)
        )
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded.raw_key}"},
            json={
                "model": "demo-chat-001",
                "messages": [{"role": "user", "content": "route me"}],
            },
        )
    assert resp.status_code == 200, resp.text

    rows = await _stats_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == OUTCOME_SUCCESS
    assert row.miner_id == miner_id
    assert row.miner_hotkey == hotkey
    assert row.epoch == stub_epoch
    assert row.model_id == "demo-chat-001"
    assert row.http_status == 200
    # The statistics row must agree with what was actually settled/billed.
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (11, 6, 17)
    # Measured, not estimated: a real forward always takes a non-negative time and
    # the field must be populated (not left None).
    assert row.latency_ms is not None and row.latency_ms >= 0
    # Nothing has reconciled against the enclave receipt chain yet.
    assert row.verified is False


async def test_streaming_success_records_ttft_and_usage(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        await _provision_real_miner(transport, base_url)
        product_routes.set_forwarder_transport(
            StubChatTransport(transport, prompt_tokens=9, completion_tokens=4)
        )
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded.raw_key}"},
            json={
                "model": "demo-chat-001",
                "messages": [{"role": "user", "content": "stream"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        assert resp.status_code == 200, resp.text
        assert "[DONE]" in resp.text

    rows = await _stats_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == OUTCOME_SUCCESS
    assert (row.prompt_tokens, row.completion_tokens) == (9, 4)
    assert row.latency_ms is not None
    # Time-to-first-token only exists for a stream, and is what makes streaming
    # responsiveness measurable at all.
    assert row.ttft_ms is not None and row.ttft_ms >= 0


# --------------------------------------------------------------------------- #
# Failure path — the behavior the DAG design could not provide
# --------------------------------------------------------------------------- #


async def test_failover_records_the_failed_attempt_and_the_success(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    """A miner that is tried and fails gets its own row, attributed to IT."""
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        good_id, good_hotkey = await _provision_real_miner(transport, base_url)
        bad_hotkey = "66" * 32
        bad_id = await _seed_unreachable_miner("unreachable.miner", hotkey=bad_hotkey)

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
                "messages": [{"role": "user", "content": "failover"}],
            },
        )
    assert resp.status_code == 200, resp.text

    rows = await _stats_rows()
    by_hotkey = {row.miner_hotkey: row for row in rows}
    assert set(by_hotkey) == {bad_hotkey, good_hotkey}

    failed = by_hotkey[bad_hotkey]
    assert failed.miner_id == bad_id
    assert failed.outcome == OUTCOME_FORWARD_ERROR
    assert failed.total_tokens == 0
    assert failed.latency_ms is not None
    assert failed.epoch == stub_epoch
    # Denormalized chain identity travels with the row.
    assert failed.subnet_node_id == 7

    served = by_hotkey[good_hotkey]
    assert served.miner_id == good_id
    assert served.outcome == OUTCOME_SUCCESS
    assert served.total_tokens == 11
    # Both rows describe the SAME request, so they share its id — that is what lets
    # the enclave receipt for this request be matched to the miner that served it.
    assert failed.request_id == served.request_id


async def test_all_candidates_failing_still_records_every_attempt(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    """A 503 to the user must not cost us the evidence of who failed."""
    seeded = await seed_api_key(credits=10_000)
    await _seed_unreachable_miner("down-a.miner", hotkey="a1" * 32)
    await _seed_unreachable_miner("down-b.miner", hotkey="b2" * 32)

    class AllDownTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("connection refused")

    product_routes.set_forwarder_transport(AllDownTransport())
    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {seeded.raw_key}"},
        json={"model": "demo-chat-001", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503, resp.text

    rows = await _stats_rows()
    assert {row.miner_hotkey for row in rows} == {"a1" * 32, "b2" * 32}
    assert all(row.outcome == OUTCOME_FORWARD_ERROR for row in rows)
    assert all(row.total_tokens == 0 for row in rows)


async def test_streaming_failover_records_the_failed_attempt(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        _good_id, good_hotkey = await _provision_real_miner(transport, base_url)
        bad_hotkey = "77" * 32
        await _seed_unreachable_miner("stream-down.miner", hotkey=bad_hotkey)

        class FailoverTransport(StubChatTransport):
            async def handle_async_request(self, request):
                if request.url.host == "stream-down.miner":
                    raise httpx.ConnectError("connection refused")
                return await super().handle_async_request(request)

        product_routes.set_forwarder_transport(
            FailoverTransport(transport, prompt_tokens=5, completion_tokens=2)
        )
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded.raw_key}"},
            json={
                "model": "demo-chat-001",
                "messages": [{"role": "user", "content": "stream failover"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        assert resp.status_code == 200, resp.text
        assert "[DONE]" in resp.text

    rows = await _stats_rows()
    by_hotkey = {row.miner_hotkey: row.outcome for row in rows}
    assert by_hotkey == {bad_hotkey: OUTCOME_FORWARD_ERROR, good_hotkey: OUTCOME_SUCCESS}


# --------------------------------------------------------------------------- #
# Outcome classification + fail-safe behavior
# --------------------------------------------------------------------------- #


async def test_forward_error_carries_outcome_latency_and_status():
    """Outcome codes are persisted, so classification belongs to the error itself."""
    timeout = ForwardError("slow", outcome=OUTCOME_TIMEOUT, latency_ms=1234)
    assert (timeout.outcome, timeout.latency_ms, timeout.http_status) == (
        OUTCOME_TIMEOUT,
        1234,
        None,
    )
    http = ForwardError("bad", outcome=OUTCOME_HTTP_ERROR, latency_ms=5, http_status=502)
    assert (http.outcome, http.http_status) == (OUTCOME_HTTP_ERROR, 502)
    # Subclasses carry their own default without the caller having to know it.
    assert NoActiveTokenError("none").outcome == "no_token"
    # A bare ForwardError still classifies as a generic forward failure.
    assert ForwardError("boom").outcome == OUTCOME_FORWARD_ERROR


async def test_recording_never_breaks_the_request_path(stub_epoch):
    """A broken statistics write must be swallowed, not raised at the user."""

    class Exploding:
        """A candidate whose attribute access fails mid-row-build."""

        miner_id = None
        miner_model_id = None
        hotkey = "de" * 32
        subnet_node_id = None
        model_id = "demo-chat-001"
        model_version = None

        @property
        def model_hash(self):
            raise RuntimeError("boom")

    async with async_session_maker() as session:
        assert (
            await stats_recorder.record_attempt(
                session, Exploding(), outcome=OUTCOME_SUCCESS
            )
            is None
        )


async def test_epoch_is_none_when_the_chain_is_unreachable():
    """An unstamped row is the correct degradation: unscorable, but not a lost request."""
    stats_recorder.set_chain_client(MockChainClient(allow_unknown=True, epoch=None))
    try:
        assert stats_recorder.current_epoch() is None
    finally:
        stats_recorder.set_chain_client(None)
