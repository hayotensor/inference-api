"""/v1/models reflects the live registry (with a dev fallback when empty)."""

from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel, ProvisionedToken
from inference_api.security import utcnow


async def test_models_dev_fallback_when_registry_empty(client, seed_api_key):
    seeded = await seed_api_key(credits=1_000)
    resp = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["data"]]
    # Empty registry + non-production -> demo fallback list.
    assert ids == ["demo-inference-001", "demo-chat-001"]


async def test_models_lists_registry_when_present(
    client, seed_api_key, seed_miner, seed_provisioned_token
):
    seeded = await seed_api_key(credits=1_000)
    miner = await seed_miner(model_ids=["llama-3-8b", "qwen-2.5-7b"])
    await seed_provisioned_token(miner_id=miner.miner_id)

    resp = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert resp.status_code == 200, resp.text
    ids = sorted(m["id"] for m in resp.json()["data"])
    assert ids == ["llama-3-8b", "qwen-2.5-7b"]
    assert all(m["owned_by"] == "talaris-miner" for m in resp.json()["data"])


async def test_models_excludes_unattested_or_untokened_miners(client, seed_api_key, seed_miner):
    seeded = await seed_api_key(credits=1_000)
    # Attested + healthy but NO active token -> excluded -> dev fallback shows.
    await seed_miner(model_ids=["secret-model"], attestation_status="attested")

    resp = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["data"]]
    assert "secret-model" not in ids
    # Falls back to demo list because nothing is fully available.
    assert ids == ["demo-inference-001", "demo-chat-001"]
