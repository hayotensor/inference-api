from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import current_active_user
from app.core.errors import forbidden
from app.db.models.admin_assignment import AdminAssignment
from app.db.models.admin_role import AdminRole
from app.db.models.user import User
from app.db.session import get_async_session


@dataclass(frozen=True)
class AdminContext:
    user: User
    roles: tuple[AdminRole, ...]
    permissions: frozenset[str]

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def has_any(self, permissions: list[str] | tuple[str, ...] | set[str] | frozenset[str]) -> bool:
        return bool(self.permissions.intersection(permissions))


async def current_admin_context(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> AdminContext:
    result = await session.execute(
        select(AdminAssignment)
        .options(selectinload(AdminAssignment.role).selectinload(AdminRole.permissions))
        .where(AdminAssignment.user_id == user.id, AdminAssignment.revoked_at.is_(None))
    )
    assignments = list(result.scalars().unique())
    roles = tuple(assignment.role for assignment in assignments if assignment.role is not None)
    permissions = frozenset(
        permission.permission
        for role in roles
        for permission in role.permissions
    )
    if not permissions:
        raise forbidden("Admin access required")
    return AdminContext(user=user, roles=roles, permissions=permissions)


def require_admin_permission(permission: str):
    async def dependency(context: AdminContext = Depends(current_admin_context)) -> AdminContext:
        if not context.has(permission):
            raise forbidden("Missing admin permission")
        return context

    return dependency


def require_admin_permission_any(permissions: list[str] | tuple[str, ...]):
    async def dependency(context: AdminContext = Depends(current_admin_context)) -> AdminContext:
        if not context.has_any(permissions):
            raise forbidden("Missing admin permission")
        return context

    return dependency
