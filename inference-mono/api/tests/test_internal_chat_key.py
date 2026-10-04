from sqlalchemy import select

from app.db.models.api_key import APIKey
from app.db.models.service_client import ServiceClientRole
from app.db.models.user import User
from app.db.session import async_session_maker
from app.service_clients.schemas import ServiceClientCreate
from app.service_clients.service import ServiceClientService


async def _mint_service_token(role: ServiceClientRole, name: str = "edge-broker") -> str:
    async with async_session_maker() as session:
        resp = await ServiceClientService(session).create_client(
            ServiceClientCreate(name=name, role=role)
        )
        await session.commit()
        return resp.token


async def _register(client, email: str) -> str:
    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "sup3r-secret-pw", "full_name": "Chat User"},
    )
    assert reg.status_code == 201, reg.text
    return reg.json()["id"]


async def _mark_verified(email: str) -> None:
    async with async_session_maker() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.is_verified = True
        session.add(user)
        await session.commit()


async def test_edge_mint_by_email_refuses_unverified_user(client):
    # HYP-27: freshly registered users are NOT verified. The internal broker
    # path is authenticated by a trusted `edge` service token, but that token
    # authenticates the broker as a caller, not the human behind the chat
    # session — it must enforce the exact same verify gate as the public
    # POST /api-keys, not bypass it. This is the regression test for the
    # "Open-WebUI mints a live key before email verify" bug.
    await _register(client, "chatuser@example.com")
    token = await _mint_service_token(ServiceClientRole.edge)

    resp = await client.post(
        "/internal/users/by-email/chat-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "chatuser@example.com"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["code"] == "forbidden"

    # No live key, and specifically no `openwebui-chat` key, was minted.
    async with async_session_maker() as session:
        user = (
            await session.execute(select(User).where(User.email == "chatuser@example.com"))
        ).scalar_one()
        keys = (
            await session.execute(select(APIKey).where(APIKey.user_id == user.id))
        ).scalars().all()
        assert keys == []


async def test_edge_mint_by_id_refuses_unverified_user(client):
    user_id = await _register(client, "byid-unverified@example.com")
    token = await _mint_service_token(ServiceClientRole.edge)

    resp = await client.post(
        f"/internal/users/{user_id}/chat-key",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.text


async def test_edge_mints_chat_key_by_email_for_verified_user(client):
    await _register(client, "verified-chatuser@example.com")
    await _mark_verified("verified-chatuser@example.com")
    token = await _mint_service_token(ServiceClientRole.edge)

    resp = await client.post(
        "/internal/users/by-email/chat-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "verified-chatuser@example.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"].startswith("sk_live_")
    assert set(body["scopes"]) == {"inference:write", "models:read"}
    assert body["name"] == "openwebui-chat"


async def test_edge_mints_chat_key_by_id(client):
    user_id = await _register(client, "byid@example.com")
    await _mark_verified("byid@example.com")
    token = await _mint_service_token(ServiceClientRole.edge)

    resp = await client.post(
        f"/internal/users/{user_id}/chat-key",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["key"].startswith("sk_live_")


async def test_remint_rotates_prior_managed_key_in_place(client):
    # A remint (e.g. the edge broker's cache TTL lapsing between chat sessions)
    # must rotate the existing row's secret rather than revoke-and-insert, or
    # every idle-then-resume session would leave a permanent extra row behind.
    await _register(client, "remint@example.com")
    await _mark_verified("remint@example.com")
    token = await _mint_service_token(ServiceClientRole.edge)
    headers = {"Authorization": f"Bearer {token}"}
    body = {"email": "remint@example.com"}

    first = await client.post("/internal/users/by-email/chat-key", headers=headers, json=body)
    second = await client.post("/internal/users/by-email/chat-key", headers=headers, json=body)
    assert first.json()["key"] != second.json()["key"]
    assert first.json()["id"] == second.json()["id"]

    async with async_session_maker() as session:
        keys = (
            await session.execute(select(APIKey).where(APIKey.name == "openwebui-chat"))
        ).scalars().all()
        assert len(keys) == 1
        assert keys[0].revoked_at is None


async def test_non_edge_token_is_rejected(client):
    await _register(client, "router@example.com")
    router_token = await _mint_service_token(ServiceClientRole.router, name="router-1")
    resp = await client.post(
        "/internal/users/by-email/chat-key",
        headers={"Authorization": f"Bearer {router_token}"},
        json={"email": "router@example.com"},
    )
    assert resp.status_code == 401


async def test_missing_token_is_rejected(client):
    resp = await client.post(
        "/internal/users/by-email/chat-key",
        json={"email": "whoever@example.com"},
    )
    assert resp.status_code == 401


async def test_unknown_user_returns_404(client):
    token = await _mint_service_token(ServiceClientRole.edge)
    resp = await client.post(
        "/internal/users/by-email/chat-key",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "nobody@example.com"},
    )
    assert resp.status_code == 404
