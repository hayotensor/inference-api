from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.admin.permissions import OWNER_ROLE
from app.admin.service import seed_default_admin_roles
from app.audit.service import write_audit_log
from app.db.models.admin_assignment import AdminAssignment
from app.db.models.admin_role import AdminRole
from app.db.models.user import User
from app.db.session import async_session_maker


async def bootstrap_owner(email: str) -> None:
    normalized_email = email.strip().lower()
    async with async_session_maker() as session:
        await seed_default_admin_roles(session)
        user = (
            await session.execute(select(User).where(func.lower(User.email) == normalized_email))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"User does not exist: {normalized_email}")
        owner_role = (
            await session.execute(select(AdminRole).where(AdminRole.slug == OWNER_ROLE))
        ).scalar_one()
        existing = (
            await session.execute(
                select(AdminAssignment).where(
                    AdminAssignment.user_id == user.id,
                    AdminAssignment.role_id == owner_role.id,
                    AdminAssignment.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(AdminAssignment(user_id=user.id, role_id=owner_role.id))
            await session.flush()
            await write_audit_log(
                session,
                "admin.owner.bootstrap",
                user_id=user.id,
                actor_user_id=user.id,
                actor="bootstrap",
                target_type="user",
                target_id=user.id,
                metadata={"email": user.email, "role_slug": OWNER_ROLE},
            )
        await session.commit()
    print(f"Owner role is assigned to {normalized_email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign the owner admin role to an existing user.")
    parser.add_argument("--email", required=True, help="Email address of an existing user.")
    args = parser.parse_args()
    asyncio.run(bootstrap_owner(args.email))


if __name__ == "__main__":
    main()
