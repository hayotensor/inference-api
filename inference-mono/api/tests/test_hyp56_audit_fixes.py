"""HYP-56 internal audit (part 1) regression tests for the control plane."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import synthetic_wallet_email, utcnow
from app.db.models.api_key import APIKey
from app.db.models.service_client import ServiceClientRole
from app.db.models.user import User
from app.db.session import async_session_maker
from app.service_clients.schemas import ServiceClientCreate
from app.service_clients.service import ServiceClientService


def _production(**overrides) -> Settings:
    values = dict(
        app_env="production",
        debug=False,
        jwt_secret="a" * 40,
        verification_token_secret="a" * 40,
        reset_password_token_secret="a" * 40,
        oauth_state_secret="a" * 40,
        session_secret="a" * 40,
        secret_pepper="a" * 40,
        sso_secret="a" * 40,
        allowed_hosts=["example.com"],
        cors_origins=["https://app.example.com"],
        rate_limit_fail_open=False,
        token_revocation_fail_open=False,
        phone_user_email_domain="phone.example.com",
        wallet_user_email_domain="wallet.example.com",
        auto_verify_new_users=False,
    )
    values.update(overrides)
    return Settings(**values)


def test_production_baseline_settings_are_accepted():
    assert _production().app_env == "production"


def test_production_refuses_auto_verify_new_users():
    with pytest.raises(ValidationError, match="AUTO_VERIFY_NEW_USERS"):
        _production(auto_verify_new_users=True)


# --------------------------------------------------------------------------- #
# Chat SSO identity must be a proven address (the SSO email becomes the
# Open-WebUI account key, and with it that account's chat history).
# --------------------------------------------------------------------------- #

async def _register_and_login(client, monkeypatch, email: str, *, verified: bool) -> str:
    async def noop_verify(self, email: str, token: str) -> None:
        return None

    monkeypatch.setattr("app.auth.service.EmailService.send_verification_email", noop_verify)
    register = await client.post("/auth/register", json={"email": email, "password": "correct-horse-123"})
    assert register.status_code == 201, register.text
    if verified:
        async with async_session_maker() as session:
            await session.execute(
                User.__table__.update().where(User.email == email).values(is_verified=True)
            )
            await session.commit()
    login = await client.post("/auth/login", json={"email": email, "password": "correct-horse-123"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def test_sso_cookie_refused_for_unverified_email(client, monkeypatch):
    token = await _register_and_login(client, monkeypatch, "claimer@example.com", verified=False)
    resp = await client.post("/auth/sso/cookie", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text


async def test_sso_cookie_issued_for_verified_email(client, monkeypatch):
    token = await _register_and_login(client, monkeypatch, "owner@example.com", verified=True)
    resp = await client.post("/auth/sso/cookie", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


async def test_sso_cookie_refused_after_email_changed_to_unproven_address(client, monkeypatch):
    token = await _register_and_login(client, monkeypatch, "mover@example.com", verified=True)
    headers = {"Authorization": f"Bearer {token}"}
    patched = await client.patch("/me", headers=headers, json={"email": "someone-elses-old@example.com"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["is_verified"] is False
    resp = await client.post("/auth/sso/cookie", headers=headers)
    assert resp.status_code == 403, resp.text


async def test_cannot_patch_email_onto_a_synthetic_wallet_address(client, monkeypatch):
    token = await _register_and_login(client, monkeypatch, "squatter@example.com", verified=True)
    resp = await client.patch(
        "/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": synthetic_wallet_email("0x" + "11" * 20)},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "email_reserved"


# --------------------------------------------------------------------------- #
# The system-managed chat key (broker-owned) is not a self-service key.
# --------------------------------------------------------------------------- #

async def _edge_mint(client, email: str) -> dict:
    async with async_session_maker() as session:
        token = (
            await ServiceClientService(session).create_client(
                ServiceClientCreate(name="edge-broker", role=ServiceClientRole.edge)
            )
        ).token
        await session.commit()
    resp = await client.post(
        "/internal/users/by-email/chat-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": email},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_managed_chat_key_is_invisible_to_self_service_routes(client, monkeypatch):
    token = await _register_and_login(client, monkeypatch, "chatty@example.com", verified=True)
    headers = {"Authorization": f"Bearer {token}"}
    managed = await _edge_mint(client, "chatty@example.com")
    key_id = managed["id"]
    assert (await client.get(f"/api-keys/{key_id}", headers=headers)).status_code == 404
    assert (await client.get(f"/api-keys/{key_id}/spend", headers=headers)).status_code == 404
    patched = await client.patch(
        f"/api-keys/{key_id}", headers=headers, json={"expires_at": (utcnow() + timedelta(minutes=5)).isoformat()}
    )
    assert patched.status_code == 404


async def test_reserved_managed_key_name_cannot_be_claimed(client, monkeypatch):
    token = await _register_and_login(client, monkeypatch, "namer@example.com", verified=True)
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api-keys", headers=headers, json={"name": "OpenWebUI-Chat", "scopes": ["inference:write"]}
    )
    assert created.status_code == 400
    assert created.json()["detail"]["code"] == "reserved_key_name"
    ok = await client.post("/api-keys", headers=headers, json={"name": "mine", "scopes": ["inference:write"]})
    assert ok.status_code == 201, ok.text
    renamed = await client.patch(f"/api-keys/{ok.json()['id']}", headers=headers, json={"name": "openwebui-chat"})
    assert renamed.status_code == 400
    assert renamed.json()["detail"]["code"] == "reserved_key_name"


async def test_managed_key_rotation_clears_leftover_restrictions(client, monkeypatch):
    await _register_and_login(client, monkeypatch, "restricted@example.com", verified=True)
    first = await _edge_mint(client, "restricted@example.com")
    async with async_session_maker() as session:
        row = await session.get(APIKey, uuid.UUID(first["id"]))
        row.expires_at = utcnow() - timedelta(minutes=1)
        row.allowed_ips = ["10.0.0.1"]
        row.allowed_models = ["nope"]
        row.limit_period = "daily"
        row.credit_limit = 1
        session.add(row)
        await session.commit()
    second = await _edge_mint(client, "restricted@example.com")
    assert second["id"] == first["id"]
    async with async_session_maker() as session:
        row = (await session.execute(select(APIKey).where(APIKey.name == "openwebui-chat"))).scalar_one()
        assert row.expires_at is None
        assert row.allowed_ips is None and row.allowed_models is None
        assert row.limit_period is None and row.credit_limit is None


# --------------------------------------------------------------------------- #
# OAuth hand-off keeps the session tokens out of the query string.
# --------------------------------------------------------------------------- #


async def test_oauth_callback_hands_tokens_over_in_the_fragment(client, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from app.auth import routes as auth_routes
    from app.auth.oauth import OAuthProfile

    class FakeOAuthClient:
        async def authorize_access_token(self, request):
            return {"access_token": "provider-token", "expires_at": 9999999999}

    async def fake_profile(provider, request, token):
        return OAuthProfile(
            provider="google",
            account_id="google-hyp56",
            email="oauth-hyp56@example.com",
            email_verified=True,
            full_name="OAuth User",
        )

    monkeypatch.setattr(auth_routes, "get_oauth_client", lambda provider: FakeOAuthClient())
    monkeypatch.setattr(auth_routes, "extract_oauth_profile", fake_profile)
    resp = await client.get("/auth/google/callback", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    location = urlsplit(resp.headers["location"])
    assert location.path == "/auth/callback/google"
    assert location.query == ""
    handoff = parse_qs(location.fragment)
    assert handoff["access_token"][0] and handoff["refresh_token"][0]
