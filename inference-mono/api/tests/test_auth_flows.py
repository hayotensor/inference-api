import asyncio

from app.db.models.user import User
from app.db.session import async_session_maker


async def test_email_register_verify_login_refresh_logout_and_password_reset(client, monkeypatch):
    verify_tokens: list[str] = []
    reset_tokens: list[str] = []

    async def capture_verify(self, email: str, token: str) -> None:
        verify_tokens.append(token)

    async def capture_reset(self, email: str, token: str) -> None:
        reset_tokens.append(token)

    monkeypatch.setattr(
        "app.auth.service.EmailService.send_verification_email",
        capture_verify,
    )
    monkeypatch.setattr(
        "app.auth.service.EmailService.send_password_reset_email",
        capture_reset,
    )

    register = await client.post(
        "/auth/register",
        json={
            "email": "dev@example.com",
            "password": "correct-horse-123",
            "full_name": "Dev User",
        },
    )
    assert register.status_code == 201
    assert verify_tokens

    verify = await client.post("/auth/verify-email", json={"token": verify_tokens[-1]})
    assert verify.status_code == 200
    assert verify.json()["is_verified"] is True

    login = await client.post(
        "/auth/login",
        json={"email": "dev@example.com", "password": "correct-horse-123"},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    me = await client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "dev@example.com"

    refresh = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 200
    rotated = refresh.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    reuse = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401
    family_revoked = await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert family_revoked.status_code == 401

    forgot = await client.post("/auth/forgot-password", json={"email": "dev@example.com"})
    assert forgot.status_code == 202
    assert reset_tokens

    reset = await client.post(
        "/auth/reset-password",
        json={"token": reset_tokens[-1], "password": "new-correct-horse-123"},
    )
    assert reset.status_code == 200
    old_access_rejected = await client.get(
        "/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert old_access_rejected.status_code == 401

    old_login = await client.post(
        "/auth/login",
        json={"email": "dev@example.com", "password": "correct-horse-123"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/auth/login",
        json={"email": "dev@example.com", "password": "new-correct-horse-123"},
    )
    assert new_login.status_code == 200

    logout_tokens = new_login.json()
    logout = await client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {logout_tokens['access_token']}"},
        json={"refresh_token": logout_tokens["refresh_token"]},
    )
    assert logout.status_code == 204
    logged_out_access_rejected = await client.get(
        "/me", headers={"Authorization": f"Bearer {logout_tokens['access_token']}"}
    )
    assert logged_out_access_rejected.status_code == 401


async def test_concurrent_refresh_allows_only_one_winner(client, monkeypatch):
    verify_tokens: list[str] = []

    async def capture_verify(self, email: str, token: str) -> None:
        verify_tokens.append(token)

    monkeypatch.setattr("app.auth.service.EmailService.send_verification_email", capture_verify)

    await client.post(
        "/auth/register",
        json={"email": "race@example.com", "password": "correct-horse-123"},
    )
    await client.post("/auth/verify-email", json={"token": verify_tokens[-1]})
    login = await client.post(
        "/auth/login",
        json={"email": "race@example.com", "password": "correct-horse-123"},
    )
    refresh_token = login.json()["refresh_token"]
    responses = await asyncio.gather(
        client.post("/auth/refresh", json={"refresh_token": refresh_token}),
        client.post("/auth/refresh", json={"refresh_token": refresh_token}),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 401]


async def test_unauthorized_user_route_rejected(client):
    response = await client.get("/me")
    assert response.status_code == 401


async def test_user_update_and_delete_me(client, monkeypatch):
    async def noop_verify(self, email: str, token: str) -> None:
        return None

    monkeypatch.setattr("app.auth.service.EmailService.send_verification_email", noop_verify)

    await client.post(
        "/auth/register",
        json={"email": "delete@example.com", "password": "correct-horse-123"},
    )
    async with async_session_maker() as session:
        user = (await session.execute(User.__table__.select())).first()
        await session.execute(
            User.__table__.update().where(User.id == user.id).values(is_verified=True)
        )
        await session.commit()

    login = await client.post(
        "/auth/login",
        json={"email": "delete@example.com", "password": "correct-horse-123"},
    )
    access_token = login.json()["access_token"]
    update = await client.patch(
        "/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"full_name": "Updated"},
    )
    assert update.status_code == 200
    assert update.json()["full_name"] == "Updated"

    delete = await client.delete("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert delete.status_code == 204


async def test_signup_survives_a_failing_email_sender(client, monkeypatch):
    """A dead or misconfigured sender must not 500 the signup form: the account row
    is already committed, so a 500 would leave the address taken with no way to
    verify it. /auth/request-verify-email is the recovery path."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "auto_verify_new_users", False)

    async def explode(self, email: str, token: str) -> None:
        raise RuntimeError("resend: 403 domain not verified")

    monkeypatch.setattr("app.email.service.EmailService.send_verification_email", explode)

    register = await client.post(
        "/auth/register",
        json={"email": "senderdown@example.com", "password": "Sender-Down-Pw!1", "full_name": "Sender Down"},
    )
    assert register.status_code == 201, register.text
    assert register.json()["is_verified"] is False

    # The account exists, so the address is not silently re-registerable...
    again = await client.post(
        "/auth/register",
        json={"email": "senderdown@example.com", "password": "Sender-Down-Pw!1", "full_name": "Dup"},
    )
    assert again.status_code == 400

    # ...and asking for a fresh verification email still answers cleanly.
    resend = await client.post(
        "/auth/request-verify-email", json={"email": "senderdown@example.com"}
    )
    assert resend.status_code == 202, resend.text


async def test_forgot_password_survives_a_failing_email_sender(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "auto_verify_new_users", True)
    await client.post(
        "/auth/register",
        json={"email": "resetdown@example.com", "password": "Reset-Down-Pw!1", "full_name": "Reset Down"},
    )

    async def explode(self, email: str, token: str) -> None:
        raise RuntimeError("resend: 429 rate limited")

    monkeypatch.setattr("app.email.service.EmailService.send_password_reset_email", explode)

    response = await client.post("/auth/forgot-password", json={"email": "resetdown@example.com"})
    assert response.status_code == 202, response.text


async def test_verification_email_can_be_requested_twice_in_the_same_second(client, monkeypatch):
    """The verify token is a JWT whose only varying input is `exp`, so two requests
    inside one second are byte-identical. Recording the hash must tolerate that or
    the resend endpoint 500s exactly when a user retries."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "auto_verify_new_users", False)
    sent: list[str] = []

    async def capture(self, email: str, token: str) -> None:
        sent.append(token)

    monkeypatch.setattr("app.email.service.EmailService.send_verification_email", capture)

    register = await client.post(
        "/auth/register",
        json={"email": "twice@example.com", "password": "Twice-Pw-1234!", "full_name": "Twice"},
    )
    assert register.status_code == 201, register.text

    for _ in range(3):
        again = await client.post("/auth/request-verify-email", json={"email": "twice@example.com"})
        assert again.status_code == 202, again.text

    assert len(sent) >= 2
    # Identical tokens are exactly the case that used to raise IntegrityError.
    verify = await client.post("/auth/verify-email", json={"token": sent[-1]})
    assert verify.status_code == 200, verify.text
    assert verify.json()["is_verified"] is True
