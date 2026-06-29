"""Miner self-registration tests (SQLite, ed25519 hotkeys).

Exercises the two-layer auth (miner rk_ token + hotkey signature verified ONLY
via talaris_contracts.verify_registration_signature), nonce replay guard, and the
chain-class gate via a MockChainClient.
"""

import secrets

from nacl.signing import SigningKey
from sqlalchemy import select

from talaris_contracts import HostedModel, MinerHealth, sign_registration_ed25519

from inference_api.chain import ChainNode, MockChainClient
from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.main import create_app
from inference_api.miners.routes import get_chain_client
from inference_api.models import Miner, MinerModel


def _signed_registration(
    signing_key: SigningKey,
    *,
    tee_endpoint: str = "https://miner-1.tee.test",
    models=None,
    nonce: str | None = None,
):
    models = models if models is not None else [HostedModel(model_id="demo-chat-001")]
    return sign_registration_ed25519(
        signing_key=signing_key,
        peer_id="12D3KooWPeer1",
        subnet_node_id=7,
        tee_endpoint=tee_endpoint,
        hosted_models=models,
        health=MinerHealth(state="online", healthy=True),
        nonce=nonce or secrets.token_hex(16),
        timestamp=1_700_000_000,
    )


async def test_register_persists_miner_and_models(client, seed_miner_token):
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(
        signing_key,
        models=[
            HostedModel(model_id="demo-chat-001"),
            HostedModel(model_id="demo-inference-001", model_version="v2"),
        ],
    )

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["hotkey"] == reg.hotkey.lower()
    assert body["attestation_status"] == "pending"
    assert sorted(body["models"]) == ["demo-chat-001", "demo-inference-001"]

    async with async_session_maker() as session:
        miner = (
            await session.execute(select(Miner).where(Miner.hotkey == reg.hotkey.lower()))
        ).scalar_one()
        assert miner.tee_endpoint == "https://miner-1.tee.test"
        assert miner.subnet_node_id == 7
        # miner_hash binds registration -> attestation (sha256 of the pubkey).
        import hashlib

        assert miner.miner_hash == hashlib.sha256(bytes.fromhex(reg.hotkey)).hexdigest()
        models = (
            await session.execute(select(MinerModel).where(MinerModel.miner_id == miner.id))
        ).scalars().all()
        assert {m.model_id for m in models} == {"demo-chat-001", "demo-inference-001"}


async def test_register_rejects_bad_signature(client, seed_miner_token):
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(signing_key)
    tampered = reg.model_copy(update={"signature": "00" * 64})

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=tampered.model_dump(),
    )
    assert resp.status_code in (401, 403), resp.text


async def test_register_requires_miner_role_token(client, seed_router_token):
    # A router-role rk_ token must NOT be accepted for miner registration.
    router_token = await seed_router_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(signing_key)
    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {router_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 401, resp.text


async def test_register_nonce_replay_rejected(client, seed_miner_token):
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    nonce = secrets.token_hex(16)
    reg = _signed_registration(signing_key, nonce=nonce)
    headers = {"Authorization": f"Bearer {miner_token.raw_token}"}

    first = await client.post("/miners/register", headers=headers, json=reg.model_dump())
    assert first.status_code == 201, first.text
    # Same signed payload (same nonce) replayed -> rejected.
    replay = await client.post("/miners/register", headers=headers, json=reg.model_dump())
    assert replay.status_code == 401, replay.text


async def test_register_chain_class_gate(client, seed_miner_token):
    """With chain_required + min_class=Included, a low-class miner is rejected."""
    miner_token = await seed_miner_token()
    signing_key_low = SigningKey.generate()
    signing_key_ok = SigningKey.generate()
    reg_low = _signed_registration(signing_key_low)
    reg_ok = _signed_registration(signing_key_ok)

    mock_chain = MockChainClient(
        {
            reg_low.hotkey.lower(): ChainNode(subnet_node_id=3, classification="Registered"),
            reg_ok.hotkey.lower(): ChainNode(subnet_node_id=4, classification="Included"),
        }
    )
    app = create_app()
    app.dependency_overrides[get_chain_client] = lambda: mock_chain

    from httpx import ASGITransport, AsyncClient

    settings.chain_required = True
    settings.chain_min_class = "Included"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            headers = {"Authorization": f"Bearer {miner_token.raw_token}"}
            low = await ac.post("/miners/register", headers=headers, json=reg_low.model_dump())
            assert low.status_code == 403, low.text
            ok = await ac.post("/miners/register", headers=headers, json=reg_ok.model_dump())
            assert ok.status_code == 201, ok.text
            assert ok.json()["chain_class"] == "Included"
    finally:
        settings.chain_required = False
        settings.chain_min_class = "Included"
