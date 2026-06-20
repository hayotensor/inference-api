from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.admin.permissions import OWNER_ROLE
from app.admin.service import seed_default_admin_roles
from app.db.models.admin_assignment import AdminAssignment
from app.db.models.admin_role import AdminRole
from app.db.models.api_key import APIKey
from app.db.models.audit_log import AuditLog
from app.db.models.user import User
from app.db.session import async_session_maker

PASSWORD = "correct-horse-123"


async def register_and_login(client, email: str) -> dict:
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": email.split("@", 1)[0]},
    )
    assert register.status_code == 201, register.text
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()


async def user_by_email(email: str) -> User:
    async with async_session_maker() as session:
        return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def assign_role(email: str, role_slug: str) -> tuple[User, AdminRole]:
    async with async_session_maker() as session:
        await seed_default_admin_roles(session)
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        role = (await session.execute(select(AdminRole).where(AdminRole.slug == role_slug))).scalar_one()
        session.add(AdminAssignment(user_id=user.id, role_id=role.id))
        await session.commit()
        return user, role


async def set_verified(email: str, *, phone_number: str | None = None) -> User:
    async with async_session_maker() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.is_verified = True
        if phone_number is not None:
            user.phone_number = phone_number
        session.add(user)
        await session.commit()
        return user


async def test_admin_auth_and_owner_access(client):
    normal = await register_and_login(client, "normal@example.com")
    unauthenticated = await client.get("/admin/me")
    assert unauthenticated.status_code == 401

    forbidden = await client.get(
        "/admin/me",
        headers={"Authorization": f"Bearer {normal['access_token']}"},
    )
    assert forbidden.status_code == 403

    owner_login = await register_and_login(client, "owner@example.com")
    await assign_role("owner@example.com", OWNER_ROLE)
    allowed = await client.get(
        "/admin/me",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert allowed.status_code == 200
    body = allowed.json()
    assert "owner" in {role["slug"] for role in body["roles"]}
    assert "admins.owner.write" in body["permissions"]


async def test_owner_can_grant_roles_but_non_owner_cannot_grant_owner(client):
    owner_login = await register_and_login(client, "owner@example.com")
    manager_login = await register_and_login(client, "manager@example.com")
    await register_and_login(client, "target@example.com")
    owner, _ = await assign_role("owner@example.com", OWNER_ROLE)
    manager = await user_by_email("manager@example.com")
    target = await user_by_email("target@example.com")

    roles = await client.get(
        "/admin/roles",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert roles.status_code == 200, roles.text
    roles_by_slug = {role["slug"]: role for role in roles.json()}

    grant_manager = await client.post(
        f"/admin/users/{manager.id}/roles",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
        json={"role_id": roles_by_slug["admin_manager"]["id"]},
    )
    assert grant_manager.status_code == 200, grant_manager.text

    grant_owner = await client.post(
        f"/admin/users/{target.id}/roles",
        headers={"Authorization": f"Bearer {manager_login['access_token']}"},
        json={"role_id": roles_by_slug["owner"]["id"]},
    )
    assert grant_owner.status_code == 403

    revoke_last_owner = await client.delete(
        f"/admin/users/{owner.id}/roles/{roles_by_slug['owner']['id']}",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert revoke_last_owner.status_code == 403


async def test_admin_user_masking_by_sensitive_permission(client):
    owner_login = await register_and_login(client, "owner@example.com")
    support_login = await register_and_login(client, "support@example.com")
    await register_and_login(client, "customer@example.com")
    await assign_role("owner@example.com", OWNER_ROLE)
    support, _ = await assign_role("support@example.com", "support")
    customer = await set_verified("customer@example.com", phone_number="+14155550123")

    owner_response = await client.get(
        f"/admin/users/{customer.id}",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert owner_response.status_code == 200, owner_response.text
    assert owner_response.json()["email"] == "customer@example.com"
    assert owner_response.json()["phone_number"] == "+14155550123"

    support_response = await client.get(
        f"/admin/users/{customer.id}",
        headers={"Authorization": f"Bearer {support_login['access_token']}"},
    )
    assert support_response.status_code == 200, support_response.text
    assert support_response.json()["email"] != "customer@example.com"
    assert support_response.json()["phone_number"] != "+14155550123"
    assert support_response.json()["id"] == str(customer.id)
    assert support.id


async def test_admin_api_key_revoke_never_returns_plaintext_and_is_audited(client):
    owner_login = await register_and_login(client, "owner@example.com")
    target_login = await register_and_login(client, "target@example.com")
    await assign_role("owner@example.com", OWNER_ROLE)
    target = await set_verified("target@example.com")

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {target_login['access_token']}"},
        json={"name": "target key", "environment": "test"},
    )
    assert created.status_code == 201, created.text
    plaintext_key = created.json()["key"]
    key_id = created.json()["id"]

    listed = await client.get(
        "/admin/api-keys",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert listed.status_code == 200, listed.text
    assert plaintext_key not in listed.text
    assert "hashed_key" not in listed.text

    revoked = await client.post(
        f"/admin/api-keys/{key_id}/revoke",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None

    async with async_session_maker() as session:
        db_key = await session.get(APIKey, UUID(key_id))
        assert db_key.revoked_at is not None
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.actor_user_id.is_not(None),
                    AuditLog.action == "admin.api_key.revoke",
                    AuditLog.target_id == key_id,
                )
            )
        ).scalar_one_or_none()
        assert audit is not None
        assert audit.user_id == target.id


async def test_admin_billing_plan_and_usage_adjustment_are_audited(client):
    owner_login = await register_and_login(client, "owner@example.com")
    await register_and_login(client, "customer@example.com")
    await assign_role("owner@example.com", OWNER_ROLE)
    customer = await set_verified("customer@example.com")

    created_plan = await client.post(
        "/admin/billing/plans",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
        json={
            "slug": "enterprise",
            "name": "Enterprise",
            "description": "Enterprise plan",
            "monthly_token_allowance": 50_000_000,
            "visible": True,
            "active": True,
            "sort_order": 100,
            "features": {"support": "enterprise"},
        },
    )
    assert created_plan.status_code == 200, created_plan.text

    deactivated = await client.post(
        f"/admin/billing/plans/{created_plan.json()['id']}/deactivate",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False
    assert deactivated.json()["visible"] is False

    adjustment = await client.post(
        f"/admin/users/{customer.id}/usage/manual-adjustments",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
        json={"amount": 1234, "reason": "test credit"},
    )
    assert adjustment.status_code == 200, adjustment.text
    assert adjustment.json()["amount"] == 1234

    audits = await client.get(
        "/admin/audit-logs?search=admin.billing_plan.deactivate",
        headers={"Authorization": f"Bearer {owner_login['access_token']}"},
    )
    assert audits.status_code == 200, audits.text
    assert audits.json()["total"] >= 1
