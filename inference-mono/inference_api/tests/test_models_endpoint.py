"""/v1/models reflects the live registry -- and only the live registry.

The old behavior invented a static ``demo-inference-001`` / ``demo-chat-001``
fallback list whenever the registry was empty or nothing was fully available.
That was the fabrication bug (see product_routes.execute_inference): it is
gone, on purpose. An empty/unavailable registry now yields an honest answer
(empty list, or the model listed as ``status="degraded"``), never invented ids.
"""

from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel, ProvisionedToken
from inference_api.security import utcnow


async def test_models_empty_registry_returns_empty_list(client, seed_api_key):
    """No miner has ever registered anything -> an honest empty list.

    Replaces the old ``test_models_dev_fallback_when_registry_empty``, which
    asserted the very fabrication bug this contract change fixed (an empty
    registry silently returning invented ``demo-inference-001`` /
    ``demo-chat-001`` ids). That behavior was deliberately removed, so the
    test is replaced rather than adjusted -- there is no intent left to
    preserve there.
    """
    seeded = await seed_api_key(credits=1_000)
    resp = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


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
    """A model with no active token is registered, but not usable.

    The intent this test protects -- an unattested/untokened miner's model
    must never be advertised as something a caller can actually get served
    -- is unchanged. What changed is the *shape* of "not usable": there is no
    more demo fallback list to fall back to, so the assertion is updated to
    the new ``status`` semantics (present in the catalog so it doesn't just
    vanish, but marked ``"degraded"`` rather than ``"available"``).
    """
    seeded = await seed_api_key(credits=1_000)
    # Attested + healthy but NO active token -> not selectable -> degraded.
    await seed_miner(model_ids=["secret-model"], attestation_status="attested")

    resp = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert resp.status_code == 200, resp.text
    models = {m["id"]: m for m in resp.json()["data"]}
    assert models.keys() == {"secret-model"}
    assert models["secret-model"]["status"] == "degraded"
