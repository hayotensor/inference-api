from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_active_user
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session
from app.referrals.schemas import ReferralCodeUpdate, ReferralSummaryRead
from app.referrals.service import ReferralService

router = APIRouter(prefix="/referrals", tags=["referrals"])


@router.get("/me", response_model=ReferralSummaryRead)
@limiter.limit("60/minute")
async def get_my_referral_summary(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> ReferralSummaryRead:
    """Returns the caller's referral code + shareable link, how many people
    they've referred, lifetime bonus tokens earned, and a short history of
    the earnings that produced that total. Generates the referral code on
    first call if the user doesn't have one yet."""
    summary = await ReferralService(session).summary(user)
    await session.commit()
    return ReferralSummaryRead.model_validate(summary)


@router.put("/me/code", response_model=ReferralSummaryRead)
@limiter.limit("10/minute")
async def set_my_referral_code(
    request: Request,
    payload: ReferralCodeUpdate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> ReferralSummaryRead:
    """HYP-93: replace the caller's referral code with a custom one (shared
    as ``{app_url}/r/<code>``). 400 if the code is malformed or reserved, 409
    if someone else already has it. The previous code keeps attributing
    signups to the caller, so links already shared with it don't break."""
    service = ReferralService(session)
    await service.set_custom_code(user, payload.code)
    summary = await service.summary(user)
    await session.commit()
    return ReferralSummaryRead.model_validate(summary)
