from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select

from inference_api.db import async_session_maker
from inference_api.models import APIKey, APIKeyUsage, InferenceUsageEvent, ServiceClient
from inference_api.security import utcnow


async def test_router_validate_reserve_settle_and_settle_retry(client, seed_api_key, seed_router_token):
    router = await seed_router_token()
    api_key = await seed_api_key(credits=1_000)
    headers = {"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": api_key.raw_key}

    validated = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=100&max_output_tokens=200",
        headers=headers,
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["allowed"] is True
    assert validated.json()["estimated_charged_tokens"] == 300
    assert validated.json()["remaining_tokens"] == 1_000

    reserved = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={
            "request_id": "router-request-1",
            "model": "small-model",
            "input_tokens": 100,
            "max_output_tokens": 200,
        },
    )
    assert reserved.status_code == 201, reserved.text
    reservation = reserved.json()
    assert reservation["status"] == "reserved"
    assert reservation["reserved_tokens"] == 300
    assert reservation["remaining_tokens"] == 700

    replayed_reserve = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={
            "request_id": "router-request-1",
            "model": "small-model",
            "input_tokens": 100,
            "max_output_tokens": 200,
        },
    )
    assert replayed_reserve.status_code == 201, replayed_reserve.text
    assert replayed_reserve.json()["reservation_id"] == reservation["reservation_id"]
    assert replayed_reserve.json()["remaining_tokens"] == 700

    settled = await client.post(
        "/router/inference/usage",
        headers=headers,
        json={
            "reservation_id": reservation["reservation_id"],
            "request_id": "router-request-1",
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == "settled"
    assert settled.json()["charged_tokens"] == 150
    assert settled.json()["remaining_tokens"] == 850

    retry = await client.post(
        "/router/inference/usage",
        headers=headers,
        json={
            "reservation_id": reservation["reservation_id"],
            "request_id": "router-request-1",
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["remaining_tokens"] == 850

    async with async_session_maker() as session:
        usage_count = (
            await session.execute(
                select(func.count(APIKeyUsage.id)).where(APIKeyUsage.request_id == "router-request-1")
            )
        ).scalar_one()
        assert usage_count == 1


async def test_router_release_and_expiration_restore_reserved_credits(client, seed_api_key, seed_router_token):
    router = await seed_router_token()
    api_key = await seed_api_key(credits=500)
    headers = {"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": api_key.raw_key}

    reserved = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={
            "request_id": "router-release-1",
            "model": "small-model",
            "input_tokens": 100,
            "max_output_tokens": 300,
        },
    )
    assert reserved.status_code == 201, reserved.text
    assert reserved.json()["remaining_tokens"] == 100

    released = await client.post(
        f"/router/inference/reservations/{reserved.json()['reservation_id']}/release",
        headers=headers,
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "released"
    assert released.json()["remaining_tokens"] == 500

    expiring = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={
            "request_id": "router-expire-1",
            "model": "small-model",
            "input_tokens": 100,
            "max_output_tokens": 300,
        },
    )
    assert expiring.status_code == 201, expiring.text
    reservation_id = UUID(expiring.json()["reservation_id"])
    async with async_session_maker() as session:
        event = await session.get(InferenceUsageEvent, reservation_id)
        event.expires_at = utcnow() - timedelta(seconds=1)
        session.add(event)
        await session.commit()

    validated = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers=headers,
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["remaining_tokens"] == 500

    expired_settle = await client.post(
        "/router/inference/usage",
        headers=headers,
        json={
            "reservation_id": str(reservation_id),
            "request_id": "router-expire-1",
            "input_tokens": 100,
            "output_tokens": 50,
        },
    )
    assert expired_settle.status_code == 400
    assert expired_settle.json()["detail"]["code"] == "reservation_not_active"

    async with async_session_maker() as session:
        event = await session.get(InferenceUsageEvent, reservation_id)
        assert event.status == "expired"


async def test_router_auth_user_key_and_insufficient_credit_failures(client, seed_api_key, seed_router_token):
    router = await seed_router_token()
    api_key = await seed_api_key()
    headers = {"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": api_key.raw_key}

    missing_router = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers={"X-User-API-Key": api_key.raw_key},
    )
    assert missing_router.status_code == 401

    invalid_router = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers={"Authorization": "Bearer rk_live_missing", "X-User-API-Key": api_key.raw_key},
    )
    assert invalid_router.status_code == 401

    invalid_user_key = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers={"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": "sk_test_missing"},
    )
    assert invalid_user_key.status_code == 401

    scoped_key = await seed_api_key(raw_key="sk_test_models_only", scopes=["models:read"])
    missing_scope = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers={"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": scoped_key.raw_key},
    )
    assert missing_scope.status_code == 403

    insufficient = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers=headers,
    )
    assert insufficient.status_code == 200, insufficient.text
    assert insufficient.json()["allowed"] is False

    rejected_reserve = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={
            "request_id": "router-insufficient-1",
            "model": "small-model",
            "input_tokens": 1,
            "max_output_tokens": 1,
        },
    )
    assert rejected_reserve.status_code == 402

    async with async_session_maker() as session:
        client_record = await session.get(ServiceClient, router.router_client_id)
        client_record.expires_at = utcnow() - timedelta(seconds=1)
        session.add(client_record)
        db_key = await session.get(APIKey, api_key.api_key_id)
        db_key.expires_at = utcnow() - timedelta(seconds=1)
        session.add(db_key)
        await session.commit()

    expired_router = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers=headers,
    )
    assert expired_router.status_code == 401
