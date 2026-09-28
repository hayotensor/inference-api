from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.dependencies import current_active_user
from app.billing.plans import WALLET_TOPUP_BUNDLES
from app.billing.schemas import (
    BillingPlanRead,
    BillingSubscriptionRead,
    CancelSubscriptionRequest,
    ChangeSubscriptionRequest,
    StripeCheckoutSessionRequest,
    StripeSessionResponse,
    StripeWebhookResponse,
    SyncCheckoutSessionRequest,
    WalletBalanceRead,
    WalletCheckoutSessionRequest,
    WalletTopupBundleRead,
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
    stripe_session = await BillingService(session).create_checkout_session(
        user, payload.plan_id, payload.interval
    )
    await write_audit_log(
        session,
        "billing.checkout_session",
        user_id=user.id,
        request=request,
        metadata={
            "plan_id": str(payload.plan_id),
            "interval": payload.interval,
            "session_id": stripe_session["id"],
        },
    )
    await session.commit()
    return StripeSessionResponse.model_validate(stripe_session)


@router.post("/stripe/checkout-session/sync", response_model=BillingSubscriptionRead)
@limiter.limit("30/minute")
async def sync_checkout_session(
    request: Request,
    payload: SyncCheckoutSessionRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BillingSubscriptionRead:
    """Confirms a just-completed Checkout Session directly with Stripe instead of
    waiting on the webhook, so /billing/success can show the new plan right away.
    The webhook still runs and lands on the same idempotent sync path."""
    result = await BillingService(session).sync_checkout_session_by_id(user, payload.session_id)
    await session.commit()
    return BillingSubscriptionRead.model_validate(result)


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


@router.get("/wallet/bundles", response_model=list[WalletTopupBundleRead])
@limiter.limit("60/minute")
async def list_wallet_topup_bundles(request: Request) -> list[WalletTopupBundleRead]:
    return [
        WalletTopupBundleRead(id=bundle.id, usd_cents=bundle.usd_cents, tokens=bundle.tokens)
        for bundle in WALLET_TOPUP_BUNDLES
    ]


@router.get("/wallet", response_model=WalletBalanceRead)
@limiter.limit("60/minute")
async def get_wallet_balance(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> WalletBalanceRead:
    balance = await BillingService(session).get_wallet_balance(user)
    return WalletBalanceRead(balance_tokens=balance)


@router.post("/wallet/checkout-session", response_model=StripeSessionResponse)
@limiter.limit("10/minute")
async def create_wallet_checkout_session(
    request: Request,
    payload: WalletCheckoutSessionRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> StripeSessionResponse:
    stripe_session = await BillingService(session).create_wallet_checkout_session(
        user, payload.bundle_id
    )
    await write_audit_log(
        session,
        "billing.wallet_checkout_session",
        user_id=user.id,
        request=request,
        metadata={"bundle_id": payload.bundle_id, "session_id": stripe_session["id"]},
    )
    await session.commit()
    return StripeSessionResponse.model_validate(stripe_session)


@router.get("/subscription", response_model=BillingSubscriptionRead)
@limiter.limit("60/minute")
async def get_subscription(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BillingSubscriptionRead:
    return BillingSubscriptionRead.model_validate(await BillingService(session).subscription_response(user))


@router.post("/subscription/change", response_model=BillingSubscriptionRead)
@limiter.limit("10/minute")
async def change_subscription(
    request: Request,
    payload: ChangeSubscriptionRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BillingSubscriptionRead:
    subscription = await BillingService(session).change_subscription_plan(
        user, payload.plan_id, payload.interval
    )
    await write_audit_log(
        session,
        "billing.subscription_change",
        user_id=user.id,
        request=request,
        metadata={
            "plan_id": str(payload.plan_id),
            "interval": payload.interval,
            "subscription_id": subscription.stripe_subscription_id,
        },
    )
    await session.commit()
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
