from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.db.models.api_key import APIKey
from app.db.session import async_session_maker


async def create_phone_verified_session(client, monkeypatch):
    monkeypatch.setattr("app.auth.phone.generate_otp_code", lambda: "123456")
    await client.post(
        "/auth/phone/request-otp",
        json={"phone_number": "+14155552680", "purpose": "login"},
    )
    response = await client.post(
        "/auth/phone/verify-otp",
        json={"phone_number": "+14155552680", "code": "123456"},
    )
    assert response.status_code == 200
    return response.json()


async def test_api_key_creation_listing_revocation_and_inference_routes_are_detached(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "name": "test key",
            "environment": "test",
            "scopes": ["models:read", "inference:write", "usage:read"],
        },
    )
    assert created.status_code == 201
    key_body = created.json()
    assert key_body["key"].startswith("sk_test_")
    assert key_body["key"] not in (await client.get(
        "/api-keys", headers={"Authorization": f"Bearer {access_token}"}
    )).text

    listed = await client.get("/api-keys", headers={"Authorization": f"Bearer {access_token}"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    removed_from_main_api = await client.get("/v1/models", headers={"Authorization": f"Bearer {key_body['key']}"})
    assert removed_from_main_api.status_code == 404

    async with async_session_maker() as db_session:
        db_key = await db_session.get(APIKey, UUID(key_body["id"]))
        assert db_key.last_used_at is None

    revoked = await client.delete(
        f"/api-keys/{key_body['id']}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert revoked.status_code == 204


async def test_api_key_expiration_and_scope_validation(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]
    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    expired = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "expired", "environment": "test", "expires_at": expired_at},
    )
    assert expired.status_code == 422

    valid_for_expiry = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "will expire", "environment": "test"},
    )
    assert valid_for_expiry.status_code == 201
    valid_body = valid_for_expiry.json()
    async with async_session_maker() as db_session:
        db_key = await db_session.get(APIKey, UUID(valid_body["id"]))
        db_key.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db_session.commit()
    assert valid_body["key"].startswith("sk_test_")

    wildcard = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "wildcard", "environment": "test", "scopes": ["*"]},
    )
    assert wildcard.status_code == 422

    unknown = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "unknown", "environment": "test", "scopes": ["billing:read"]},
    )
    assert unknown.status_code == 422

    scoped = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": "limited", "environment": "test", "scopes": ["models:read"]},
    )
    assert scoped.status_code == 201


async def test_api_key_creation_requires_verified_account(client, monkeypatch):
    async def noop_verify(self, email: str, token: str) -> None:
        return None

    monkeypatch.setattr("app.auth.service.EmailService.send_verification_email", noop_verify)
    await client.post(
        "/auth/register",
        json={"email": "unverified@example.com", "password": "correct-horse-123"},
    )
    login = await client.post(
        "/auth/login",
        json={"email": "unverified@example.com", "password": "correct-horse-123"},
    )
    response = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"name": "blocked", "environment": "test"},
    )
    assert response.status_code == 403
