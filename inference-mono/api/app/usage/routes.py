from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_active_user
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session
from app.core.errors import not_found
from app.usage.schemas import (
    UsageAllowanceRead,
    UsageByModelDetailResponse,
    UsageByModelResponse,
    UsageHistoryResponse,
    UsageHourlyResponse,
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


@router.get("/hourly", response_model=UsageHourlyResponse)
@limiter.limit("60/minute")
async def usage_hourly(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsageHourlyResponse:
    period, buckets = await UsageService(session).usage_hourly(user)
    await session.commit()
    return UsageHourlyResponse(
        period_start=period.period_start, period_end=period.period_end, data=buckets
    )


@router.get("/by-model", response_model=UsageByModelResponse)
@limiter.limit("60/minute")
async def usage_by_model(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsageByModelResponse:
    rows, total = await UsageService(session).usage_by_model(user, limit=limit, offset=offset)
    return UsageByModelResponse(data=rows, total=total, limit=limit, offset=offset)


@router.get("/by-model/{model}", response_model=UsageByModelDetailResponse)
@limiter.limit("60/minute")
async def usage_by_model_detail(
    request: Request,
    model: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> UsageByModelDetailResponse:
    result = await UsageService(session).usage_by_model_detail(user, model, limit=limit, offset=offset)
    if result is None:
        raise not_found("No usage found for this model")
    summary, events = result
    return UsageByModelDetailResponse(
        summary=summary,
        events=events,
        total_events=summary["request_count"],
        limit=limit,
        offset=offset,
    )


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
