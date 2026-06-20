from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_active_user
from app.auth.service import UserManager, get_user_manager
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session
from app.users.schemas import UserRead, UserUpdate
from app.users.service import UserService

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(current_active_user)) -> User:
    return user


@router.patch("/me", response_model=UserRead)
@limiter.limit("30/minute")
async def update_me(
    request: Request,
    payload: UserUpdate,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
    user: User = Depends(current_active_user),
) -> User:
    updated = await UserService(session, user_manager).update_me(user, payload, request)
    await session.commit()
    return updated


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def delete_me(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
    user: User = Depends(current_active_user),
) -> Response:
    await UserService(session, user_manager).delete_me(user, request)
    await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
