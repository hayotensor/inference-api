"""Receipt reconciliation + signed epoch snapshots.

Drives the real dev-TEE ``/usage/receipts`` endpoint (real hash chain, real enclave
head signature) so the reconciliation half is genuinely exercised, then finalizes
and asserts the properties the whole consensus design depends on:

* only tokens corroborated by BOTH sides are reward-bearing;
* ``finalize`` is idempotent and re-serves byte-identical signed bytes;
* the served artifact verifies under the pinned platform key.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from nacl.signing import SigningKey

from talaris_contracts import MinerEpochStatsArtifact, verify_miner_stats

from inference_api import product_routes, stats_recorder
from inference_api.chain import MockChainClient
from inference_api.db import async_session_maker
from inference_api.miners.receipts import (
    SKIP_BAD_HEAD_SIGNATURE,
    SKIP_USAGE_ROLLBACK,
    SKIP_VERIFY_KEY_MISMATCH,
    TeeReceiptsCollector,
)
from inference_api.models import Miner, MinerEpochSnapshot, MinerModel, MinerRequestStat
from inference_api.provisioner.key import provisioner_verify_key_hex
from inference_api.routing.forwarder import OUTCOME_FORWARD_ERROR, OUTCOME_SUCCESS
from inference_api.stats_service import MinerEpochStatsService, percentile_ms

from _tee import StubChatTransport, dev_tee_app, record_dev_tee_usage
from test_routing_forward import _provision_real_miner


TEST_EPOCH = 41


@pytest.fixture
def stub_epoch():
    stats_recorder.set_chain_client(MockChainClient(allow_unknown=True, epoch=TEST_EPOCH))
    yield TEST_EPOCH
    stats_recorder.set_chain_client(None)


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


async def _add_attempt(
    session,
    *,
    hotkey: str,
    miner_id=None,
    request_id: str,
    model_id: str = "demo-chat-001",
    outcome: str = OUTCOME_SUCCESS,
    prompt_tokens: int = 11,
    completion_tokens: int = 6,
    latency_ms: int = 100,
    verified: bool = False,
    epoch: int = TEST_EPOCH,
    subnet_node_id: int | None = 7,
) -> MinerRequestStat:
    row = MinerRequestStat(
        id=uuid.uuid4(),
        epoch=epoch,
        miner_id=miner_id,
        miner_hotkey=hotkey,
        subnet_node_id=subnet_node_id,
        model_id=model_id,
        request_id=request_id,
        outcome=outcome,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        verified=verified,
    )
    session.add(row)
    await session.flush()
    return row


# --------------------------------------------------------------------------- #
# Reconciliation against the real enclave receipt chain
# --------------------------------------------------------------------------- #


async def test_reconciliation_verifies_only_matching_requests(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    """A receipt verifies an attempt only when ids AND token counts agree."""
    seeded = await seed_api_key(credits=10_000)

    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)

        matching = "req-matching"
        mismatched = "req-token-mismatch"
        undispatched = "req-never-dispatched"

        # Enclave side: three real chained rows.
        await record_dev_tee_usage(
            transport, request_id=matching, prompt_tokens=11, completion_tokens=6
        )
        await record_dev_tee_usage(
            transport, request_id=mismatched, prompt_tokens=999, completion_tokens=999
        )
        await record_dev_tee_usage(
            transport, request_id=undispatched, prompt_tokens=1, completion_tokens=1
        )

        async with async_session_maker() as session:
            # Platform side: two dispatches, one of which disagrees on tokens.
            await _add_attempt(
                session, hotkey=hotkey, miner_id=miner_id, request_id=matching
            )
            await _add_attempt(
                session,
                hotkey=hotkey,
                miner_id=miner_id,
                request_id=mismatched,
                prompt_tokens=11,
                completion_tokens=6,
            )
            await session.commit()

        async with async_session_maker() as session:
            summary = await TeeReceiptsCollector(session, transport=transport).collect_all()
            await session.commit()

    assert summary.models_skipped == 0, [r.skipped for r in summary.results]
    assert summary.rows_verified == 1
    result = summary.results[0]
    assert result.rows_fetched == 3
    # The token-mismatch and the never-dispatched receipt are both counted, not trusted.
    assert result.rows_unmatched == 2

    async with async_session_maker() as session:
        rows = {
            r.request_id: r.verified
            for r in (await session.execute(select(MinerRequestStat))).scalars()
        }
        assert rows == {matching: True, mismatched: False}

        # The durable cursor advanced and the cumulative anchor was refreshed, so the
        # next pass does not re-read the whole chain.
        model = (
            await session.execute(
                select(MinerModel).where(MinerModel.miner_id == miner_id)
            )
        ).scalars().first()
        assert model.receipt_cursor_row_id == 3
        assert model.usage_count == 3
        assert model.usage_chain_head


async def test_cursor_means_a_second_pass_fetches_nothing(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)
        await record_dev_tee_usage(transport, request_id="req-1")
        async with async_session_maker() as session:
            await _add_attempt(session, hotkey=hotkey, miner_id=miner_id, request_id="req-1")
            await session.commit()

        async with async_session_maker() as session:
            first = await TeeReceiptsCollector(session, transport=transport).collect_all()
            await session.commit()
        async with async_session_maker() as session:
            second = await TeeReceiptsCollector(session, transport=transport).collect_all()
            await session.commit()

    assert first.rows_verified == 1
    assert second.results[0].rows_fetched == 0
    assert second.rows_verified == 0


async def test_verify_key_mismatch_refuses_the_pull(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    """Only the key captured at ATTESTATION may sign the head."""
    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)
        await record_dev_tee_usage(transport, request_id="req-1")

        async with async_session_maker() as session:
            await _add_attempt(session, hotkey=hotkey, miner_id=miner_id, request_id="req-1")
            # Pretend attestation captured a different enclave key.
            model = (
                await session.execute(select(MinerModel).where(MinerModel.miner_id == miner_id))
            ).scalars().first()
            model.enclave_verify_key = bytes(SigningKey.generate().verify_key).hex()
            miner = await session.get(Miner, miner_id)
            miner.enclave_verify_key = model.enclave_verify_key
            await session.commit()

        async with async_session_maker() as session:
            summary = await TeeReceiptsCollector(session, transport=transport).collect_all()
            await session.commit()

    assert [r.skipped for r in summary.results] == [SKIP_VERIFY_KEY_MISMATCH]
    assert summary.rows_verified == 0
    async with async_session_maker() as session:
        row = (await session.execute(select(MinerRequestStat))).scalars().one()
        assert row.verified is False


async def test_tampered_head_signature_refuses_the_pull(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)
        await record_dev_tee_usage(transport, request_id="req-1")
        async with async_session_maker() as session:
            await _add_attempt(session, hotkey=hotkey, miner_id=miner_id, request_id="req-1")
            await session.commit()

        class TamperTransport(httpx.AsyncBaseTransport):
            """Flip the enclave's head signature on the way back."""

            def __init__(self, inner):
                self._inner = inner

            async def handle_async_request(self, request):
                response = await self._inner.handle_async_request(request)
                if not request.url.path.startswith("/usage/receipts"):
                    return response
                await response.aread()
                body = json.loads(response.content)
                body["head_signature"] = "00" * 64
                return httpx.Response(200, json=body)

        async with async_session_maker() as session:
            summary = await TeeReceiptsCollector(
                session, transport=TamperTransport(transport)
            ).collect_all()
            await session.commit()

    assert [r.skipped for r in summary.results] == [SKIP_BAD_HEAD_SIGNATURE]
    async with async_session_maker() as session:
        row = (await session.execute(select(MinerRequestStat))).scalars().one()
        assert row.verified is False


async def test_usage_rollback_refuses_the_pull(
    client, seed_api_key, reset_forwarder_transport, stub_epoch
):
    """A cumulative counter that went BACKWARD means a replaced/rolled-back store."""
    async with dev_tee_app() as (transport, base_url):
        miner_id, hotkey = await _provision_real_miner(transport, base_url)
        await record_dev_tee_usage(transport, request_id="req-1")

        async with async_session_maker() as session:
            await _add_attempt(session, hotkey=hotkey, miner_id=miner_id, request_id="req-1")
            model = (
                await session.execute(select(MinerModel).where(MinerModel.miner_id == miner_id))
            ).scalars().first()
            # We previously observed a HIGHER count than the enclave now reports.
            model.usage_count = 500
            await session.commit()

        async with async_session_maker() as session:
            summary = await TeeReceiptsCollector(session, transport=transport).collect_all()
            await session.commit()

    assert [r.skipped for r in summary.results] == [SKIP_USAGE_ROLLBACK]
    async with async_session_maker() as session:
        row = (await session.execute(select(MinerRequestStat))).scalars().one()
        assert row.verified is False
        model = (
            await session.execute(select(MinerModel).where(MinerModel.miner_id == miner_id))
        ).scalars().first()
        # The stored anchor is NOT walked backward by a rollback attempt.
        assert model.usage_count == 500


# --------------------------------------------------------------------------- #
# Aggregation + signing
# --------------------------------------------------------------------------- #


async def test_finalize_counts_only_verified_tokens_and_records_failures(stub_epoch):
    hotkey = "aa" * 32
    async with async_session_maker() as session:
        await _add_attempt(session, hotkey=hotkey, request_id="v1", verified=True, latency_ms=100)
        await _add_attempt(session, hotkey=hotkey, request_id="v2", verified=True, latency_ms=300)
        # Served but never corroborated by the enclave: billed, not rewarded.
        await _add_attempt(session, hotkey=hotkey, request_id="u1", verified=False, latency_ms=200)
        await _add_attempt(
            session,
            hotkey=hotkey,
            request_id="f1",
            outcome=OUTCOME_FORWARD_ERROR,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=5,
        )
        await session.commit()

    async with async_session_maker() as session:
        artifact = await MinerEpochStatsService(session).finalize(TEST_EPOCH)
        await session.commit()

    assert len(artifact.entries) == 1
    entry = artifact.entries[0]
    assert entry.hotkey == hotkey
    assert entry.subnet_node_id == 7
    assert (entry.requests_ok, entry.requests_failed) == (3, 1)
    # Two verified successes at 11/6 each; the unverified one contributes NOTHING.
    assert entry.verified_prompt_tokens == 22
    assert entry.verified_completion_tokens == 12
    assert entry.verified_total_tokens == 34
    # p95 over SERVED latencies only (100, 200, 300) — the 5 ms connect failure must
    # not flatter the miner.
    assert entry.latency_p95_ms == 300


async def test_finalize_is_idempotent_and_bytes_are_stable(stub_epoch):
    async with async_session_maker() as session:
        await _add_attempt(session, hotkey="bb" * 32, request_id="r1", verified=True)
        await session.commit()

    async with async_session_maker() as session:
        first = await MinerEpochStatsService(session).finalize(TEST_EPOCH)
        await session.commit()

    async with async_session_maker() as session:
        # New rows arriving after finalization must NOT change a published epoch.
        await _add_attempt(session, hotkey="cc" * 32, request_id="r2", verified=True)
        await session.commit()

    async with async_session_maker() as session:
        service = MinerEpochStatsService(session)
        second = await service.finalize(TEST_EPOCH)
        fetched = await service.get(TEST_EPOCH)

    assert second.signature == first.signature
    assert second.version == first.version
    assert second.canonical_message() == first.canonical_message()
    assert fetched.canonical_message() == first.canonical_message()
    # Still only the one miner that existed at finalization time.
    assert [e.hotkey for e in fetched.entries] == ["bb" * 32]

    async with async_session_maker() as session:
        snapshots = (await session.execute(select(MinerEpochSnapshot))).scalars().all()
        assert len(snapshots) == 1


async def test_finalize_signature_verifies_under_the_pinned_platform_key(stub_epoch):
    async with async_session_maker() as session:
        await _add_attempt(session, hotkey="dd" * 32, request_id="r1", verified=True)
        await session.commit()
    async with async_session_maker() as session:
        artifact = await MinerEpochStatsService(session).finalize(TEST_EPOCH)
        await session.commit()

    # The SAME key validators already pin for /allowlist.
    assert verify_miner_stats(artifact, provisioner_verify_key_hex()) is True
    assert verify_miner_stats(artifact, bytes(SigningKey.generate().verify_key).hex()) is False


async def test_finalize_due_respects_the_settle_lag(stub_epoch):
    async with async_session_maker() as session:
        await _add_attempt(session, hotkey="ee" * 32, request_id="r1", epoch=TEST_EPOCH)
        await session.commit()

    async with async_session_maker() as session:
        service = MinerEpochStatsService(session)
        # current_epoch == TEST_EPOCH + 1 means TEST_EPOCH is the newest CLOSED epoch,
        # but a lag of 1 holds it back for one more epoch.
        assert await service.finalize_due(
            current_epoch=TEST_EPOCH + 1, settle_lag_epochs=1, max_per_pass=8
        ) == []
        await session.commit()

    async with async_session_maker() as session:
        service = MinerEpochStatsService(session)
        assert await service.finalize_due(
            current_epoch=TEST_EPOCH + 2, settle_lag_epochs=1, max_per_pass=8
        ) == [TEST_EPOCH]
        await session.commit()

    async with async_session_maker() as session:
        service = MinerEpochStatsService(session)
        # Already finalized: a later pass does nothing.
        assert await service.finalize_due(
            current_epoch=TEST_EPOCH + 5, settle_lag_epochs=1, max_per_pass=8
        ) == []


async def test_percentile_is_nearest_rank_and_dependency_free():
    """Signed into the artifact, so it must be exactly reproducible."""
    assert percentile_ms([], 95) is None
    assert percentile_ms([7], 95) == 7
    assert percentile_ms([100, 200, 300], 50) == 200
    assert percentile_ms([100, 200, 300], 95) == 300
    assert percentile_ms(list(range(1, 101)), 95) == 95
    assert percentile_ms([300, 100, 200], 50) == 200  # order-insensitive
    assert percentile_ms([None, 5, None], 50) == 5  # missing measurements ignored


# --------------------------------------------------------------------------- #
# The public endpoint
# --------------------------------------------------------------------------- #


async def test_stats_endpoint_404s_before_finalization_then_serves_signed_bytes(
    client, stub_epoch
):
    resp = await client.get(f"/subnet/stats/{TEST_EPOCH}")
    assert resp.status_code == 404, resp.text
    assert (await client.get("/subnet/stats/latest")).status_code == 404

    async with async_session_maker() as session:
        await _add_attempt(session, hotkey="ff" * 32, request_id="r1", verified=True)
        await session.commit()
    async with async_session_maker() as session:
        finalized = await MinerEpochStatsService(session).finalize(TEST_EPOCH)
        await session.commit()

    resp = await client.get(f"/subnet/stats/{TEST_EPOCH}")
    assert resp.status_code == 200, resp.text
    served = MinerEpochStatsArtifact.model_validate(resp.json())
    # Unauthenticated, but self-authenticating: verifiable against the pinned key.
    assert verify_miner_stats(served, provisioner_verify_key_hex()) is True
    assert served.canonical_message() == finalized.canonical_message()

    latest = await client.get("/subnet/stats/latest")
    assert latest.status_code == 200
    assert latest.json()["epoch"] == TEST_EPOCH

    # Two reads of a finalized epoch are byte-identical — the property validators
    # depend on to agree with each other.
    again = await client.get(f"/subnet/stats/{TEST_EPOCH}")
    assert again.json() == resp.json()


async def test_stats_endpoint_needs_no_auth(client, stub_epoch):
    """Deliberate: the body is signed, so transport auth would add nothing."""
    async with async_session_maker() as session:
        await _add_attempt(session, hotkey="ab" * 32, request_id="r1", verified=True)
        await session.commit()
    async with async_session_maker() as session:
        await MinerEpochStatsService(session).finalize(TEST_EPOCH)
        await session.commit()

    resp = await client.get(f"/subnet/stats/{TEST_EPOCH}")
    assert resp.status_code == 200
    assert "authorization" not in {k.lower() for k in resp.request.headers}
