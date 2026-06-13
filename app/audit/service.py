import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_log import AuditLog


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host


async def write_audit_log(
    session: AsyncSession,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    request: Request | None = None,
    actor: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            actor=actor,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent") if request else None,
            metadata_=metadata or {},
        )
    )
