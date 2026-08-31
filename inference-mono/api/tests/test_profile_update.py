from types import SimpleNamespace

from fastapi_users.db import SQLAlchemyUserDatabase

from app.auth.oauth import OAuthProfile, OAuthService
from app.auth.service import AuthService, UserManager
from app.db.models.oauth_account import OAuthAccount
from app.db.models.user import User
from app.db.session import async_session_maker


class DummyClient:
    host = "testclient"


class DummyURL:
    path = "/auth/oauth"


def dummy_request():
    return SimpleNamespace(client=DummyClient(), headers={}, url=DummyURL(), method="GET")


async def _oauth_login(email: str) -> str:
    """Link a Google account to a (new) user and return an access token for it."""
    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        user_manager = UserManager(user_db)
        auth_service = AuthService(session, user_manager)
        oauth_service = OAuthService(session, auth_service)
        result = await oauth_service.login_or_create(
            OAuthProfile(
                provider="google",
                account_id="google-settings-test",
                email=email,
                email_verified=True,
                full_name="Google User",
            ),
            {"access_token": "google-token", "expires_at": 9999999999},
            dummy_request(),
        )
        await session.commit()
        return result.access_token


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


async def test_get_me_reports_linked_oauth_providers(client):
    access_token = await _oauth_login("googley@example.com")
    me = await client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.status_code == 200
    assert me.json()["oauth_providers"] == ["google"]


async def test_oauth_linked_user_cannot_change_email(client):
    access_token = await _oauth_login("locked@example.com")

    response = await client.patch(
        "/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"email": "somewhere-else@example.com"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "email_locked_to_oauth"

    me = await client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me.json()["email"] == "locked@example.com"


async def test_oauth_linked_user_can_still_update_other_fields(client):
    access_token = await _oauth_login("rename-ok@example.com")

    response = await client.patch(
        "/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"full_name": "New Name", "email": "rename-ok@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "New Name"


async def test_password_user_can_still_change_email(client, monkeypatch):
    session = await create_phone_verified_session(client, monkeypatch)
    access_token = session["access_token"]
    assert session["user"]["oauth_providers"] == []

    response = await client.patch(
        "/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"email": "changed@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "changed@example.com"
