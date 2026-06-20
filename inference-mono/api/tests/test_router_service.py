from __future__ import annotations

from sqlalchemy import select

from app.admin.permissions import OWNER_ROLE
from app.admin.service import seed_default_admin_roles
from app.db.models.admin_assignment import AdminAssignment
from app.db.models.admin_role import AdminRole
from app.db.models.user import User
from app.db.session import async_session_maker

PASSWORD = "correct-horse-123"


async def create_owner_access_token(client) -> str:
    await client.post(
        "/auth/register",
        json={"email": "owner@example.com", "password": PASSWORD, "full_name": "Owner"},
    )
    login = await client.post("/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    async with async_session_maker() as session:
        await seed_default_admin_roles(session)
        user = (await session.execute(select(User).where(User.email == "owner@example.com"))).scalar_one()
        role = (await session.execute(select(AdminRole).where(AdminRole.slug == OWNER_ROLE))).scalar_one()
        session.add(AdminAssignment(user_id=user.id, role_id=role.id))
        await session.commit()
    return login.json()["access_token"]


async def test_admin_can_create_list_and_revoke_router_service_clients(client):
    owner_token = await create_owner_access_token(client)

    created = await client.post(
        "/admin/service-clients",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"name": "router one", "role": "router"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("rk_live_")
    assert body["role"] == "router"

    listed = await client.get(
        "/admin/service-clients",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert body["token"] not in listed.text
    assert "hashed_token" not in listed.text

    revoked = await client.post(
        f"/admin/service-clients/{body['id']}/revoke",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None

    removed_from_main_api = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers={"Authorization": f"Bearer {body['token']}", "X-User-API-Key": "sk_test_missing"},
    )
    assert removed_from_main_api.status_code == 404
