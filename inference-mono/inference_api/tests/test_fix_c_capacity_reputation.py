"""C3 regression: self-reported miner capacity is bounded, and routing
reputation is a platform-observed signal — not something a miner can set on
itself via heartbeat.

Pre-fix, ``record_heartbeat`` stored ``payload.capacity`` verbatim and
``MinerSelector`` read ``capacity["reputation"]`` straight off it, so a
heartbeat with ``available_concurrent_requests: 999999, queue_depth: -999,
reputation: 999.0`` was accepted verbatim and would dominate routing. These
tests FAIL against that behavior and PASS with the
``miners.service._sanitize_capacity`` write-side clamp plus the
``routing.selector`` read-side re-clamp / platform-observed reputation.
"""

from __future__ import annotations

import secrets
import uuid

import pytest_asyncio
from nacl.signing import SigningKey

import inference_api.redis as redis_module
from talaris_contracts import HostedModel, MinerHealth, sign_registration_ed25519

from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel, MinerRequestStat, ProvisionedToken
from inference_api.routing.selector import MinerSelector
from inference_api.security import utcnow


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_client_per_test():
    """See the identical fixture in test_fix_c_miner_authz.py: works around a
    pytest-asyncio (one event loop per test) vs. ``get_redis_client()``
    (one cached client per process) test-harness artifact, unrelated to the
    fix under test."""
    if redis_module._redis_client is not None:
        try:
            await redis_module._redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        redis_module._redis_client = None
    yield


def _signed_registration(signing_key: SigningKey):
    return sign_registration_ed25519(
        signing_key=signing_key,
        peer_id="12D3KooWPeerC3",
        subnet_node_id=11,
        tee_endpoint="https://miner-1.tee.test",
        hosted_models=[HostedModel(model_id="demo-chat-001")],
        health=MinerHealth(state="online", healthy=True),
        nonce=secrets.token_hex(16),
        timestamp=1_700_000_400,
    )


async def test_heartbeat_clamps_absurd_capacity_and_drops_self_reported_reputation(
    client, seed_miner_token
):
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(signing_key)

    reg_resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert reg_resp.status_code == 201, reg_resp.text
    miner_id = reg_resp.json()["miner_id"]

    hb_resp = await client.post(
        f"/miners/{miner_id}/heartbeat",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json={
            "health": {"state": "online", "healthy": True},
            "capacity": {
                "available_concurrent_requests": 999999,
                "queue_depth": -999,
                "reputation": 999.0,
            },
        },
    )
    assert hb_resp.status_code == 200, hb_resp.text
    capacity = hb_resp.json()["capacity"]
    assert "reputation" not in capacity
    assert 0 <= capacity["available_concurrent_requests"] <= 1024
    assert capacity["available_concurrent_requests"] < 999999
    assert capacity["queue_depth"] == 0


async def _seed_attested_miner(session, *, hotkey: str, capacity: dict, model_id: str = "demo-chat-001"):
    from inference_api.crypto import encrypt_token

    miner = Miner(
        id=uuid.uuid4(),
        hotkey=hotkey,
        tee_endpoint=f"http://{hotkey}.test",
        attestation_status="attested",
        attestation_mode="dev",
        health="healthy",
        tls_cert_fingerprint="cc" * 32,
        enclave_verify_key="dd" * 32,
        capacity=capacity,
        last_seen=utcnow(),
        registered_at=utcnow(),
    )
    session.add(miner)
    session.add(
        MinerModel(
            id=uuid.uuid4(),
            miner_id=miner.id,
            model_id=model_id,
            attestation_status="attested",
            loaded=True,
            last_advertised_at=utcnow(),
        )
    )
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


async def test_selector_clamps_already_corrupted_capacity_row():
    """Simulates capacity written BEFORE the write-side sanitizer existed (or
    by any path that bypasses it) — the read-side clamp in the selector must
    still stop it from dominating routing."""
    async with async_session_maker() as session:
        gamed_id = await _seed_attested_miner(
            session,
            hotkey="aa" * 32,
            capacity={
                "available_concurrent_requests": 999999,
                "queue_depth": -999,
                "reputation": 999.0,
            },
        )
        honest_id = await _seed_attested_miner(
            session,
            hotkey="bb" * 32,
            capacity={"available_concurrent_requests": 4, "queue_depth": 0},
        )
        await session.commit()

        candidates = await MinerSelector(session).select("demo-chat-001")

    by_id = {c.miner_id: c for c in candidates}
    assert by_id[gamed_id].available_concurrent_requests <= 1024
    assert by_id[gamed_id].queue_depth == 0
    # No self-reported reputation of 999 survives into the candidate.
    assert by_id[gamed_id].reputation != 999.0
    assert by_id[honest_id].reputation != 999.0


async def test_selector_reputation_is_platform_observed_not_self_reported():
    """Two miners report identical (sane) capacity; only their PLATFORM-
    observed track record (``MinerRequestStat`` rows) differs. The one with a
    worse observed success rate must rank behind, even though it self-reports
    a "perfect" ``reputation`` — which is now ignored entirely."""
    async with async_session_maker() as session:
        good_id = await _seed_attested_miner(
            session,
            hotkey="cc" * 32,
            capacity={"available_concurrent_requests": 4, "queue_depth": 0, "reputation": 0.1},
        )
        bad_id = await _seed_attested_miner(
            session,
            hotkey="dd" * 32,
            capacity={"available_concurrent_requests": 4, "queue_depth": 0, "reputation": 999.0},
        )
        for _ in range(5):
            session.add(
                MinerRequestStat(
                    id=uuid.uuid4(),
                    miner_id=good_id,
                    miner_hotkey="cc" * 32,
                    model_id="demo-chat-001",
                    outcome="success",
                    created_at=utcnow(),
                )
            )
        for i in range(4):
            session.add(
                MinerRequestStat(
                    id=uuid.uuid4(),
                    miner_id=bad_id,
                    miner_hotkey="dd" * 32,
                    model_id="demo-chat-001",
                    outcome="success" if i == 0 else "http_error",
                    created_at=utcnow(),
                )
            )
        await session.commit()

        candidates = await MinerSelector(session).select("demo-chat-001")

    ranked_ids = [c.miner_id for c in candidates]
    assert ranked_ids.index(good_id) < ranked_ids.index(bad_id)
