"""Regression test for B1: disabling a user must revoke their API key access.

Before the fix, ``APIKeyService.authenticate_key`` only checked the key's own
``revoked_at``/``expires_at`` columns and never consulted the owning ``User.is_active``
flag, so a disabled user's still-valid ``sk_`` key kept working across every endpoint
behind ``require_api_key``. This asserts the fix on two independent endpoints
(``/v1/models`` and ``/v1/chat/completions``) plus a spot check on ``/v1/inference``
and ``/v1/usage``, and confirms the error shape matches
``RouterInferenceService._authenticate_user_api_key``'s existing
``forbidden("API key owner is inactive")`` behavior (403, code "forbidden").
"""


async def test_disabled_user_key_rejected_on_models_endpoint(client, seed_api_key):
    seeded = await seed_api_key(raw_key="sk_test_disabled_models", credits=10_000, user_active=False)

    response = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "code": "forbidden",
        "message": "API key owner is inactive",
    }


async def test_disabled_user_key_rejected_on_chat_completions(client, seed_api_key):
    seeded = await seed_api_key(raw_key="sk_test_disabled_chat", credits=10_000, user_active=False)

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {seeded.raw_key}"},
        json={
            "model": "demo-chat-001",
            "messages": [{"role": "user", "content": "hello platform"}],
            "max_tokens": 32,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "code": "forbidden",
        "message": "API key owner is inactive",
    }


async def test_disabled_user_key_rejected_on_inference_and_usage(client, seed_api_key):
    seeded = await seed_api_key(raw_key="sk_test_disabled_inference", credits=10_000, user_active=False)

    inference = await client.post(
        "/v1/inference",
        headers={"X-API-Key": seeded.raw_key},
        json={"prompt": "hello platform"},
    )
    assert inference.status_code == 403, inference.text
    assert inference.json()["detail"]["code"] == "forbidden"

    usage = await client.get(
        "/v1/usage", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert usage.status_code == 403, usage.text
    assert usage.json()["detail"]["code"] == "forbidden"


async def test_active_user_key_still_works(client, seed_api_key):
    """Sanity check: the fix must not reject keys whose owner is still active."""
    seeded = await seed_api_key(raw_key="sk_test_active_still_ok", credits=10_000, user_active=True)

    response = await client.get(
        "/v1/models", headers={"Authorization": f"Bearer {seeded.raw_key}"}
    )
    assert response.status_code == 200, response.text
