from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_active_user
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session
from app.usage.schemas import (
    UsageAllowanceRead,
    UsageHistoryResponse,
    UsagePeriodRead,
    UsageRecalculateRequest,
)
from app.usage.service import UsageService

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/current", response_model=UsagePeriodRead)
@limiter.limit("60/minute")
async def current_usage(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsagePeriodRead:
    period = await UsageService(session).current_period(user)
    await session.commit()
    return UsagePeriodRead.model_validate(period)


@router.get("/history", response_model=UsageHistoryResponse)
@limiter.limit("60/minute")
async def usage_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsageHistoryResponse:
    events = await UsageService(session).usage_history(user, limit=limit)
    return UsageHistoryResponse(data=events)


@router.get("/allowance", response_model=UsageAllowanceRead)
@limiter.limit("60/minute")
async def usage_allowance(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsageAllowanceRead:
    allowance = await UsageService(session).allowance(user)
    await session.commit()
    return UsageAllowanceRead.model_validate(allowance)


@router.post("/recalculate", response_model=UsagePeriodRead)
@limiter.limit("20/minute")
async def recalculate_usage(
    request: Request,
    payload: UsageRecalculateRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsagePeriodRead:
    if payload and payload.include_balance_sync:
        from app.wallets.service import WalletService

        await WalletService(session).sync_balances(user)
    period = await UsageService(session).recalculate_current_period(user)
    await session.commit()
    return UsagePeriodRead.model_validate(period)
