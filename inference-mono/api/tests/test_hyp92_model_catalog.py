"""HYP-92: public model catalog (`GET /models`) behind the dashboard's /models page.

Pins: it is unauthenticated, lists real models only (registered by a
non-revoked miner or actively allowlisted -- never the seeded placeholder
pricing rows), joins each model's HYP-36 credit rate (1x when unpriced, same as
billing), applies the same operational/degraded/down rule as the serving
plane's `/status/models`, and derives provider/family/category/size from ids.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.catalog.metadata import derive_metadata
from app.db.models.miner import Miner, MinerModel, ProvisionedToken
from app.db.models.model_allowlist import ModelAllowlist
from app.db.models.model_pricing import ModelPricing
from app.db.session import async_session_maker


async def _seed_miner(
    model_id: str, *, attestation_status: str = "attested", health: str = "healthy", token: bool = True
) -> None:
    async with async_session_maker() as session:
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=uuid.uuid4().hex,
            tee_endpoint="http://miner.test",
            attestation_status=attestation_status,
            health=health,
        )
        session.add(miner)
        await session.flush()
        session.add(
            MinerModel(
                id=uuid.uuid4(), miner_id=miner.id, model_id=model_id,
                attestation_status=attestation_status, loaded=True,
            )
        )
        if token:
            session.add(
                ProvisionedToken(
                    miner_id=miner.id, model_id=model_id, key_id="k1",
                    encrypted_token=b"sealed", status="active",
                )
            )
        await session.commit()


async def test_catalog_is_public_and_lists_real_models_with_status_and_pricing(client):
    await _seed_miner("meta-llama/Llama-3.3-70B-Instruct")
    await _seed_miner("meta-llama/Llama-3.3-70B-Instruct")
    await _seed_miner("Qwen/Qwen2.5-Coder-32B-Instruct", health="unhealthy")
    await _seed_miner("deepseek-ai/DeepSeek-R1", attestation_status="revoked")
    async with async_session_maker() as session:
        session.add(ModelAllowlist(model_id="BAAI/bge-m3", model_hash="ab" * 32, label="embeddings", active=True))
        session.add(ModelPricing(model_name="meta-llama/Llama-3.3-70B-Instruct", token_multiplier=Decimal("2.5")))
        session.add(ModelPricing(model_name="small-model", token_multiplier=Decimal("1")))
        await session.commit()

    response = await client.get("/models")  # no Authorization header
    assert response.status_code == 200
    body = response.json()
    assert body["generated_at"]
    rows = {row["id"]: row for row in body["models"]}

    # Revoked-only model and seeded placeholder pricing never surface.
    assert set(rows) == {
        "BAAI/bge-m3",
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "meta-llama/Llama-3.3-70B-Instruct",
    }

    llama = rows["meta-llama/Llama-3.3-70B-Instruct"]
    assert llama["status"] == "operational"
    assert llama["healthy_miner_count"] == 2
    assert llama["registered_miner_count"] == 2
    assert Decimal(llama["token_multiplier"]) == Decimal("2.5")
    assert (llama["provider"], llama["family"], llama["category"], llama["parameter_size"]) == (
        "Meta", "Llama", "chat", "70B",
    )

    coder = rows["Qwen/Qwen2.5-Coder-32B-Instruct"]
    assert coder["status"] == "degraded"
    assert coder["category"] == "code"
    assert Decimal(coder["token_multiplier"]) == Decimal("1")

    bge = rows["BAAI/bge-m3"]
    assert bge["status"] == "down"
    assert bge["registered_miner_count"] == 0
    assert bge["label"] == "embeddings"
    assert bge["category"] == "embedding"


async def test_catalog_is_read_only(client):
    assert (await client.post("/models", json={})).status_code == 405


@pytest.mark.parametrize(
    ("model_id", "provider", "family", "category", "size"),
    [
        ("Qwen/Qwen3-32B", "Qwen", "Qwen", "chat", "32B"),
        ("deepseek-ai/DeepSeek-R1-Distill-Llama-70B", "DeepSeek", "DeepSeek", "reasoning", "70B"),
        ("Qwen/Qwen2.5-VL-7B-Instruct", "Qwen", "Qwen", "vision", "7B"),
        ("mistralai/Mixtral-8x7B-Instruct-v0.1", "Mistral AI", "Mistral", "chat", "8x7B"),
        ("openai/whisper-large-v3", "OpenAI", "Whisper", "audio", None),
        ("gpt-oss-20b", "OpenAI", "GPT-OSS", "chat", "20B"),
        ("some-lab/mystery-model", "some-lab", None, "chat", None),
        ("bare-model", None, None, "chat", None),
    ],
)
def test_derive_metadata(model_id, provider, family, category, size):
    meta = derive_metadata(model_id)
    assert (meta.provider, meta.family, meta.category, meta.parameter_size) == (
        provider, family, category, size,
    )
