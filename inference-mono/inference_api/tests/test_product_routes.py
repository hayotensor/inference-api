from datetime import timedelta

from inference_api.db import async_session_maker
from inference_api.models import APIKey
from inference_api.security import utcnow


async def test_models_inference_and_usage_summary(client, seed_api_key):
    seeded = await seed_api_key(credits=10_000)

    models = await client.get("/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"})
    assert models.status_code == 200, models.text
    assert [model["id"] for model in models.json()["data"]] == ["demo-inference-001", "demo-chat-001"]

    async with async_session_maker() as session:
        db_key = await session.get(APIKey, seeded.api_key_id)
        assert db_key.last_used_at is not None

    inference = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"prompt": "hello platform"},
    )
    assert inference.status_code == 200, inference.text
    body = inference.json()
    assert body["output"] == "Echo: hello platform"
    assert body["usage"]["input_tokens"] == 2
    assert body["usage"]["output_tokens"] == 3
    assert body["usage"]["remaining_tokens"] == 9_995

    usage = await client.get("/v1/usage", headers={"Authorization": f"Bearer {seeded.raw_key}"})
    assert usage.status_code == 200, usage.text
    assert usage.json()["api_key_id"] == str(seeded.api_key_id)
    assert usage.json()["requests"] >= 3
    assert usage.json()["input_tokens"] == 2
    assert usage.json()["output_tokens"] == 3


async def test_api_key_scope_expiration_and_revocation_failures(client, seed_api_key):
    wildcard = await seed_api_key(raw_key="sk_test_wildcard", scopes=["*"])
    wildcard_response = await client.get("/v1/models", headers={"Authorization": f"Bearer {wildcard.raw_key}"})
    assert wildcard_response.status_code == 403

    scoped = await seed_api_key(raw_key="sk_test_models_only", scopes=["models:read"], credits=10_000)
    allowed = await client.get("/v1/models", headers={"Authorization": f"Bearer {scoped.raw_key}"})
    assert allowed.status_code == 200
    forbidden = await client.post(
        "/v1/inference",
        headers={"Authorization": f"Bearer {scoped.raw_key}"},
        json={"prompt": "hello"},
    )
    assert forbidden.status_code == 403

    expired = await seed_api_key(raw_key="sk_test_expired", expires_at=utcnow() - timedelta(minutes=1))
    expired_response = await client.get("/v1/models", headers={"Authorization": f"Bearer {expired.raw_key}"})
    assert expired_response.status_code == 401

    revoked = await seed_api_key(raw_key="sk_test_revoked", revoked_at=utcnow())
    revoked_response = await client.get("/v1/models", headers={"Authorization": f"Bearer {revoked.raw_key}"})
    assert revoked_response.status_code == 401
