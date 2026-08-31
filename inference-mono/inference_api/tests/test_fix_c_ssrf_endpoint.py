"""C2 regression: POST /miners/register must reject a ``tee_endpoint`` (or
per-model advertised endpoint) the platform should never dial, BEFORE it is
ever persisted — closing the SSRF hole reproduced live via
``tee_endpoint="http://169.254.169.254/latest/meta-data/"`` (the cloud
instance-metadata address).

FAILS against the pre-fix route (registration succeeds with 201 for the
metadata address). PASSES with the ``validate_tee_endpoint`` guard added to
``inference_api.miners.routes.register_miner``.
"""

from __future__ import annotations

import secrets

import pytest_asyncio
from nacl.signing import SigningKey
from sqlalchemy import select

import inference_api.redis as redis_module
from talaris_contracts import HostedModel, MinerHealth, sign_registration_ed25519


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

from inference_api.db import async_session_maker
from inference_api.models import Miner


def _signed_registration(signing_key: SigningKey, *, tee_endpoint: str, models=None):
    models = models if models is not None else [HostedModel(model_id="demo-chat-001")]
    return sign_registration_ed25519(
        signing_key=signing_key,
        peer_id="12D3KooWPeerC2",
        subnet_node_id=9,
        tee_endpoint=tee_endpoint,
        hosted_models=models,
        health=MinerHealth(state="online", healthy=True),
        nonce=secrets.token_hex(16),
        timestamp=1_700_000_300,
    )


async def test_register_rejects_cloud_metadata_link_local_endpoint(client, seed_miner_token):
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(
        signing_key, tee_endpoint="http://169.254.169.254/latest/meta-data/"
    )

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 400, resp.text

    # And nothing was persisted for it either.
    async with async_session_maker() as session:
        miner = (
            await session.execute(select(Miner).where(Miner.hotkey == reg.hotkey.lower()))
        ).scalar_one_or_none()
        assert miner is None


async def test_register_rejects_link_local_hosted_model_endpoint(client, seed_miner_token):
    """The per-model advertised endpoint is dialed by the provisioner too, so
    it must be validated exactly like the top-level tee_endpoint."""
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(
        signing_key,
        tee_endpoint="https://miner-1.tee.test",
        models=[
            HostedModel(
                model_id="demo-chat-001",
                tee_endpoint="http://169.254.169.254/latest/meta-data/",
            )
        ],
    )

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 400, resp.text


async def test_register_still_allows_dev_stack_loopback_endpoint(client, seed_miner_token):
    """The live dev stack legitimately registers http://127.0.0.1:8801 —
    app_env=test (non-production) must keep allowing loopback by default."""
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(signing_key, tee_endpoint="http://127.0.0.1:8801")

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 201, resp.text


async def test_register_rejects_non_http_scheme(client, seed_miner_token):
    miner_token = await seed_miner_token()
    signing_key = SigningKey.generate()
    reg = _signed_registration(signing_key, tee_endpoint="file:///etc/passwd")

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(),
    )
    assert resp.status_code == 400, resp.text
