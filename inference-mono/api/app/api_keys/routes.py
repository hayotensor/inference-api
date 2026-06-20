import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.schemas import APIKeyCreate, APIKeyCreateResponse, APIKeyRead, APIKeyUpdate
from app.api_keys.service import APIKeyService
from app.audit.service import write_audit_log
from app.auth.dependencies import current_active_user
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_api_key(
    request: Request,
    payload: APIKeyCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> APIKeyCreateResponse:
    response = await APIKeyService(session).create_key(user, payload)
    await write_audit_log(
        session,
        "api_key.create",
        user_id=user.id,
        request=request,
        metadata={"api_key_id": str(response.id), "environment": response.environment.value},
    )
    await session.commit()
    return response


@router.get("", response_model=list[APIKeyRead])
@limiter.limit("60/minute")
async def list_api_keys(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    return await APIKeyService(session).list_keys(user)


@router.get("/{key_id}", response_model=APIKeyRead)
@limiter.limit("60/minute")
async def get_api_key(
    request: Request,
    key_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    return await APIKeyService(session).get_key(user, key_id)


@router.patch("/{key_id}", response_model=APIKeyRead)
@limiter.limit("30/minute")
async def update_api_key(
    request: Request,
    key_id: uuid.UUID,
    payload: APIKeyUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    db_key = await APIKeyService(session).update_key(user, key_id, payload)
    await write_audit_log(
        session,
        "api_key.update",
        user_id=user.id,
        request=request,
        metadata={"api_key_id": str(db_key.id)},
    )
    await session.commit()
    return db_key


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def delete_api_key(
    request: Request,
    key_id: uuid.UUID,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> Response:
    db_key = await APIKeyService(session).revoke_key(user, key_id)
    await write_audit_log(
        session,
        "api_key.revoke",
        user_id=user.id,
        request=request,
        metadata={"api_key_id": str(db_key.id)},
    )
    await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
