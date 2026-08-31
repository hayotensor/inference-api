"""C1 regression: a miner-role service token may only read/write the miner it
legitimately registered (hotkey signature verified) — not any other miner.

Pre-fix, ``current_miner_client`` alone gated heartbeat/`/me` (proves "some
valid miner-role token", not WHICH miner), so ANY miner-role token could
heartbeat (and so mutate health/capacity for) or read the private state
(``enclave_verify_key``, ``tee_endpoint``, ``tls_cert_fingerprint``, capacity)
of an unrelated miner. These tests FAIL against that behavior (attacker calls
return 200 instead of 401) and PASS with the ownership-binding fix in
``inference_api.miners.auth`` (``bind_client_to_miner`` / ``require_bound_miner``).
"""

from __future__ import annotations

import secrets

import pytest_asyncio
from nacl.signing import SigningKey

import inference_api.redis as redis_module
from talaris_contracts import HostedModel, MinerHealth, sign_registration_ed25519


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_client_per_test():
    """Force ``inference_api.redis``'s module-level client to be re-created for
    each test.

    Unrelated to the fix under test: pytest-asyncio gives each test function
    its own event loop, but ``get_redis_client()`` caches ONE client for the
    whole process — fine in production (one process, one loop, forever), but
    across enough sequential tests in one pytest session a stale connection
    bound to an already-closed prior test's loop surfaces as ``RuntimeError:
    Event loop is closed``. These tests exercise redis (the new ownership
    binding) on every request, several requests per test, so they hit that
    test-harness artifact quickly. Not fixed in ``inference_api/redis.py``
    itself (not an owned file for this change) — scoped locally instead.
    """
    if redis_module._redis_client is not None:
        try:
            await redis_module._redis_client.aclose()
        except Exception:  # noqa: BLE001 - best-effort close of a possibly-dead client
            pass
        redis_module._redis_client = None
    yield


def _signed_registration(signing_key: SigningKey, *, subnet_node_id: int = 7):
    return sign_registration_ed25519(
        signing_key=signing_key,
        peer_id="12D3KooWPeerC1",
        subnet_node_id=subnet_node_id,
        tee_endpoint="https://miner-1.tee.test",
        hosted_models=[HostedModel(model_id="demo-chat-001")],
        health=MinerHealth(state="online", healthy=True),
        nonce=secrets.token_hex(16),
        timestamp=1_700_000_200,
    )


async def _register(client, raw_token: str, signing_key: SigningKey):
    reg = _signed_registration(signing_key)
    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json(), reg


async def test_heartbeat_rejects_token_not_bound_to_target_miner(client, seed_miner_token):
    victim_token = await seed_miner_token(raw_token="rk_live_c1_victim")
    attacker_token = await seed_miner_token(raw_token="rk_live_c1_attacker")

    victim_body, _victim_reg = await _register(client, victim_token.raw_token, SigningKey.generate())
    # The attacker token is a REAL credential too — just for a different miner.
    await _register(client, attacker_token.raw_token, SigningKey.generate())

    resp = await client.post(
        f"/miners/{victim_body['miner_id']}/heartbeat",
        headers={"Authorization": f"Bearer {attacker_token.raw_token}"},
        json={"health": {"state": "offline", "healthy": False}, "capacity": {}},
    )
    assert resp.status_code == 401, resp.text


async def test_me_rejects_token_not_bound_to_target_hotkey(client, seed_miner_token):
    victim_token = await seed_miner_token(raw_token="rk_live_c1_victim2")
    attacker_token = await seed_miner_token(raw_token="rk_live_c1_attacker2")

    _victim_body, victim_reg = await _register(client, victim_token.raw_token, SigningKey.generate())
    await _register(client, attacker_token.raw_token, SigningKey.generate())

    resp = await client.get(
        "/miners/me",
        params={"hotkey": victim_reg.hotkey},
        headers={"Authorization": f"Bearer {attacker_token.raw_token}"},
    )
    assert resp.status_code == 401, resp.text
    # No private miner state should have leaked into the error body either.
    assert "enclave_verify_key" not in resp.text


async def test_legitimate_owner_can_still_heartbeat_and_read_self(client, seed_miner_token):
    """The non-adversarial path this fix must NOT break: a miner heartbeating
    and reading its own state with the token it registered with."""
    token = await seed_miner_token(raw_token="rk_live_c1_owner")
    body, reg = await _register(client, token.raw_token, SigningKey.generate())

    hb = await client.post(
        f"/miners/{body['miner_id']}/heartbeat",
        headers={"Authorization": f"Bearer {token.raw_token}"},
        json={
            "health": {"state": "online", "healthy": True},
            "capacity": {"available_concurrent_requests": 3, "queue_depth": 0},
        },
    )
    assert hb.status_code == 200, hb.text

    me = await client.get(
        "/miners/me",
        params={"hotkey": reg.hotkey},
        headers={"Authorization": f"Bearer {token.raw_token}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["hotkey"] == reg.hotkey.lower()


async def test_reregistration_rebinds_a_different_token_to_same_hotkey(client, seed_miner_token):
    """A miner rotating its service token (same hotkey signing key, new rk_
    token) must be able to re-register and heartbeat with the NEW token —
    ownership follows the signature, not the token. The OLD token then loses
    heartbeat access to that miner."""
    old_token = await seed_miner_token(raw_token="rk_live_c1_old")
    new_token = await seed_miner_token(raw_token="rk_live_c1_new")
    signing_key = SigningKey.generate()

    body, _reg = await _register(client, old_token.raw_token, signing_key)
    await _register(client, new_token.raw_token, signing_key)

    hb_new = await client.post(
        f"/miners/{body['miner_id']}/heartbeat",
        headers={"Authorization": f"Bearer {new_token.raw_token}"},
        json={"health": {"state": "online", "healthy": True}, "capacity": {}},
    )
    assert hb_new.status_code == 200, hb_new.text

    hb_old = await client.post(
        f"/miners/{body['miner_id']}/heartbeat",
        headers={"Authorization": f"Bearer {old_token.raw_token}"},
        json={"health": {"state": "online", "healthy": True}, "capacity": {}},
    )
    assert hb_old.status_code == 401, hb_old.text


async def test_heartbeat_rejects_unregistered_miner_role_token(client, seed_miner_token, seed_miner):
    """A freshly minted miner-role token that never registered anything must
    not be able to heartbeat a miner that exists from a DIFFERENT flow (e.g.
    seeded directly, mirroring the already-live production miner)."""
    fresh_token = await seed_miner_token(raw_token="rk_live_c1_fresh")
    seeded = await seed_miner()

    resp = await client.post(
        f"/miners/{seeded.miner_id}/heartbeat",
        headers={"Authorization": f"Bearer {fresh_token.raw_token}"},
        json={"health": {"state": "online", "healthy": True}, "capacity": {}},
    )
    assert resp.status_code == 401, resp.text
