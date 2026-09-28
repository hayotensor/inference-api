"""HYP-76: unlimited prompts for subscribers on the Unlimited plan.

A subscriber on a plan with `BillingPlan.is_unlimited=True` must never be
capped or 402ed by the allowance in `UsageService.reserve_inference_tokens`/
`settle_inference_usage`, regardless of volume, and must never draw against
the HYP-66 pay-as-you-go wallet either. Usage events are still written (for
observability), they just never gate anything.

Mirrors the direct-UsageService style of tests/test_hyp36_model_pricing.py and
tests/test_wallet_topup.py.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.billing.plans import seed_default_billing_plans
from app.core.config import settings
from app.core.security import utcnow
from app.db.models.billing_plan import BillingPlan
from app.db.models.user import User
from app.db.models.user_subscription import UserSubscription
from app.db.models.user_wallet import UserWallet
from app.db.session import async_session_maker
from app.usage.service import UsageService


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def _seed_unlimited_subscriber(session) -> User:
    await seed_default_billing_plans(session)
    plan = (
        await session.execute(select(BillingPlan).where(BillingPlan.slug == "unlimited"))
    ).scalar_one()
    assert plan.is_unlimited is True

    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="not-a-real-hash",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        created_at=utcnow(),
    )
    session.add(user)
    await session.flush()

    now = utcnow()
    session.add(
        UserSubscription(
            id=uuid.uuid4(),
            user_id=user.id,
            stripe_subscription_id="sub_test_unlimited",
            stripe_price_id=plan.stripe_price_id,
            plan_id=plan.id,
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    await session.flush()
    return user


def test_unlimited_plan_is_seeded_with_no_allowance_but_the_marker_set():
    """monthly_token_allowance is 0 for this plan -- it must never be read as a
    literal zero-token cap; is_unlimited is what actually governs it."""
    from app.billing.plans import default_plan_seeds

    seed = next(s for s in default_plan_seeds() if s.slug == "unlimited")
    assert seed.is_unlimited is True
    assert seed.price_cents > 0
    # Priced above Business, the previous top tier.
    business = next(s for s in default_plan_seeds() if s.slug == "business")
    assert seed.price_cents > business.price_cents


@pytest.mark.asyncio
async def test_unlimited_subscriber_is_never_blocked_regardless_of_volume():
    async with async_session_maker() as session:
        user = await _seed_unlimited_subscriber(session)
        service = UsageService(session)

        # An enormous reservation, far beyond every paid tier's allowance,
        # must sail through with no 402.
        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="small-model",
            prompt_tokens=500_000_000,
            max_completion_tokens=500_000_000,
        )
        assert event.reserved_tokens == 1_000_000_000
        assert event.wallet_reserved_tokens == 0

        settled = await service.settle_inference_usage(
            event, prompt_tokens=500_000_000, completion_tokens=750_000_000
        )
        assert settled.charged_tokens == 1_250_000_000
        assert settled.wallet_reserved_tokens == 0
        assert settled.status == "settled"

        # A second, equally huge reservation right after must also go through --
        # this is not a one-time grace amount, it never caps.
        second = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="small-model",
            prompt_tokens=2_000_000_000,
            max_completion_tokens=0,
        )
        assert second.wallet_reserved_tokens == 0

        # Usage was still recorded for observability.
        period = await service.current_period(user)
        assert period.used_tokens >= 1_250_000_000 + 2_000_000_000

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one_or_none()
        assert wallet is None or wallet.balance_tokens == 0


@pytest.mark.asyncio
async def test_unlimited_subscriber_does_not_draw_from_an_existing_wallet_balance():
    """Even if this user happens to have wallet funds (e.g. from before
    subscribing), an unlimited plan must never spend them."""
    async with async_session_maker() as session:
        user = await _seed_unlimited_subscriber(session)
        session.add(UserWallet(user_id=user.id, balance_tokens=10_000))
        await session.flush()
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=999_999, max_completion_tokens=0
        )
        assert event.wallet_reserved_tokens == 0

        await service.settle_inference_usage(event, prompt_tokens=999_999, completion_tokens=0)

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 10_000
