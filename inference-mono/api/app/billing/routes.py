from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.dependencies import current_active_user
from app.billing.schemas import (
    BillingPlanRead,
    BillingSubscriptionRead,
    CancelSubscriptionRequest,
    StripeCheckoutSessionRequest,
    StripeSessionResponse,
    StripeWebhookResponse,
)
from app.billing.service import BillingService
from app.core.errors import bad_request
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[BillingPlanRead])
@limiter.limit("60/minute")
async def list_billing_plans(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> list[BillingPlanRead]:
    plans = await BillingService(session).list_plans()
    await session.commit()
    return [BillingPlanRead.model_validate(plan) for plan in plans]


@router.post("/stripe/checkout-session", response_model=StripeSessionResponse)
@limiter.limit("10/minute")
async def create_checkout_session(
    request: Request,
    payload: StripeCheckoutSessionRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> StripeSessionResponse:
    stripe_session = await BillingService(session).create_checkout_session(user, payload.plan_id)
    await write_audit_log(
        session,
        "billing.checkout_session",
        user_id=user.id,
        request=request,
        metadata={"plan_id": str(payload.plan_id), "session_id": stripe_session["id"]},
    )
    await session.commit()
    return StripeSessionResponse.model_validate(stripe_session)


@router.post("/stripe/customer-portal", response_model=StripeSessionResponse)
@limiter.limit("10/minute")
async def create_customer_portal_session(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> StripeSessionResponse:
    stripe_session = await BillingService(session).create_customer_portal_session(user)
    await write_audit_log(
        session,
        "billing.customer_portal",
        user_id=user.id,
        request=request,
        metadata={"session_id": stripe_session["id"]},
    )
    await session.commit()
    return StripeSessionResponse.model_validate(stripe_session)


@router.post("/stripe/webhook", response_model=StripeWebhookResponse)
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    session: AsyncSession = Depends(get_async_session),
) -> StripeWebhookResponse:
    if not stripe_signature:
        raise bad_request("missing_stripe_signature", "Stripe-Signature header is required")
    payload = await request.body()
    processed = await BillingService(session).process_webhook(payload, stripe_signature)
    await session.commit()
    return StripeWebhookResponse(received=True, processed=processed)


@router.get("/subscription", response_model=BillingSubscriptionRead)
@limiter.limit("60/minute")
async def get_subscription(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BillingSubscriptionRead:
    return BillingSubscriptionRead.model_validate(await BillingService(session).subscription_response(user))


@router.post("/subscription/cancel", response_model=BillingSubscriptionRead)
@limiter.limit("10/minute")
async def cancel_subscription(
    request: Request,
    payload: CancelSubscriptionRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BillingSubscriptionRead:
    payload = payload or CancelSubscriptionRequest()
    subscription = await BillingService(session).cancel_subscription(
        user,
        cancel_at_period_end=payload.cancel_at_period_end,
    )
    await write_audit_log(
        session,
        "billing.subscription_cancel",
        user_id=user.id,
        request=request,
        metadata={
            "subscription_id": subscription.stripe_subscription_id,
            "cancel_at_period_end": subscription.cancel_at_period_end,
        },
    )
    await session.commit()
    return BillingSubscriptionRead.model_validate(await BillingService(session).subscription_response(user))
