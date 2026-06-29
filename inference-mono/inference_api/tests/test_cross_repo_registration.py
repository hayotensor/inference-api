"""CROSS-REPO integration: the inference-subnet miner's REAL self-registration
client must produce a payload the inference-api REAL /miners/register accepts.

Both repos stay separate and share only ``talaris_contracts`` — this test proves
they tie together at that seam (same canonical signing bytes, same model), by
running the subnet's actual ``InferenceApiRegistrationClient.build_registration()``
(loaded by file path so it never imports the libp2p server stack) against the
inference-api app + DB via the existing test fixtures.
"""

import hashlib
import importlib.util
import pathlib

import pytest
from nacl.signing import SigningKey

_SUBNET_CLIENT = pathlib.Path(
    "/home/rizzo/talaris-inference/inference-subnet/inference/network_api/"
    "inference_api_registration.py"
)


def _load_subnet_client():
    """Load the subnet registration client module standalone (no package __init__,
    so no libp2p import is triggered)."""
    spec = importlib.util.spec_from_file_location(
        "subnet_inference_api_registration", _SUBNET_CLIENT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(
    not _SUBNET_CLIENT.exists(), reason="inference-subnet repo not present"
)


async def test_subnet_client_payload_accepted_by_inference_api(client, seed_miner_token):
    subnet = _load_subnet_client()
    miner_token = await seed_miner_token()
    hotkey = SigningKey.generate()

    # The subnet client sources its advertised inventory from the same provider that
    # feeds gossip; mix a bare str and a dict model to exercise its coercion too.
    def inventory():
        return {
            "tee_endpoint": "https://miner-x.tee.test:8000",
            "hosted_models": [
                "demo-chat-001",
                {"model_id": "demo-inference-001", "model_version": "v2"},
            ],
            "tees": [{"tee_id": "tee-0", "status": "ready"}],
            "tee_count": 1,
            "available_tee_count": 1,
        }

    reg_client = subnet.InferenceApiRegistrationClient(
        inference_api_url="http://unused.local",
        peer_id="12D3KooWCrossRepo",
        subnet_node_id=11,
        tee_endpoint="https://miner-x.tee.test:8000",
        ed25519_signing_key=hotkey,
        inventory_provider=inventory,
    )

    # The subnet builds + signs the SelfRegistration via the shared talaris_contracts
    # helper; the inference-api verifies it via the SAME shared helper.
    reg = reg_client.build_registration()

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=reg.model_dump(mode="json"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["hotkey"] == reg.hotkey.lower()
    assert body["subnet_node_id"] == 11
    assert sorted(body["models"]) == ["demo-chat-001", "demo-inference-001"]

    # The registration->attestation bind: miner_hash == sha256(pubkey), the value the
    # TEE puts in the attestation `miner` field.
    expected_hash = hashlib.sha256(bytes.fromhex(reg.hotkey)).hexdigest()
    from sqlalchemy import select

    from inference_api.db import async_session_maker
    from inference_api.models import Miner

    async with async_session_maker() as session:
        miner = (
            await session.execute(select(Miner).where(Miner.hotkey == reg.hotkey.lower()))
        ).scalar_one()
        assert miner.miner_hash == expected_hash
        assert miner.tee_endpoint == "https://miner-x.tee.test:8000"


async def test_subnet_client_tampered_payload_rejected(client, seed_miner_token):
    """Tampering the subnet-built payload after signing must be rejected by the API's
    shared-helper verification — proving the API really verifies the signature."""
    subnet = _load_subnet_client()
    miner_token = await seed_miner_token()
    hotkey = SigningKey.generate()

    reg_client = subnet.InferenceApiRegistrationClient(
        inference_api_url="http://unused.local",
        peer_id="12D3KooWCrossRepo2",
        subnet_node_id=12,
        tee_endpoint="https://miner-y.tee.test:8000",
        ed25519_signing_key=hotkey,
    )
    reg = reg_client.build_registration()
    tampered = reg.model_copy(update={"tee_endpoint": "https://evil.tee.test:8000"})

    resp = await client.post(
        "/miners/register",
        headers={"Authorization": f"Bearer {miner_token.raw_token}"},
        json=tampered.model_dump(mode="json"),
    )
    assert resp.status_code in (401, 403), resp.text
