"""Internal, service-authenticated surface for the trusted edge gateway.

These endpoints are NOT part of the public API. They are guarded by an ``edge``
service token (``rk_…``) and MUST remain network-isolated (not routable from the
public edge). The edge gateway uses them to obtain a per-user managed chat key so
Open-WebUI traffic bills the right Talaris account through the unchanged
``/v1/chat/completions`` path.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.schemas import APIKeyCreateResponse
from app.api_keys.service import APIKeyService
from app.audit.service import write_audit_log
from app.core.errors import not_found
from app.db.models.service_client import ServiceClient
from app.db.models.user import User
from app.db.session import get_async_session
from app.service_clients.dependencies import current_edge_client

router = APIRouter(prefix="/internal", tags=["internal"])


class ChatKeyByEmailRequest(BaseModel):
    email: EmailStr


async def _mint_for_user(
    session: AsyncSession,
    user: User,
    edge_client: ServiceClient,
) -> APIKeyCreateResponse:
    response = await APIKeyService(session).mint_managed_chat_key(user)
    await write_audit_log(
        session,
        "internal.chat_key_mint",
        user_id=user.id,
        metadata={"service_client_id": str(edge_client.id)},
    )
    await session.commit()
    return response


# NOTE: the literal /by-email route must be declared before the /{user_id}
# route, otherwise "by-email" is captured as a (non-UUID) user_id path param.
@router.post("/users/by-email/chat-key", response_model=APIKeyCreateResponse)
async def mint_chat_key_by_email(
    payload: ChatKeyByEmailRequest,
    session: AsyncSession = Depends(get_async_session),
    edge_client: ServiceClient = Depends(current_edge_client),
) -> APIKeyCreateResponse:
    result = await session.execute(
        select(User).where(func.lower(User.email) == payload.email.lower())
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise not_found("User not found")
    return await _mint_for_user(session, user, edge_client)


@router.post("/users/{user_id}/chat-key", response_model=APIKeyCreateResponse)
async def mint_chat_key_by_id(
    user_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    edge_client: ServiceClient = Depends(current_edge_client),
) -> APIKeyCreateResponse:
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise not_found("User not found")
    return await _mint_for_user(session, user, edge_client)
