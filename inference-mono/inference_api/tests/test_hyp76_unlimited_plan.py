"""HYP-76: unlimited prompts for subscribers (serving plane).

Mirrors ../../api/tests/test_hyp76_unlimited_plan.py against this plane's own
copy (`inference_api/usage.py`, `inference_api/billing_plans.py`). This is the
plane that actually enforces quota on live inference traffic, so an
unlimited-plan subscriber must never be capped/402ed here regardless of
volume, and must never draw against the HYP-66 wallet either.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from inference_api.billing_plans import seed_default_billing_plans
from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.models import BillingPlan, User, UserSubscription, UserWallet
from inference_api.security import utcnow
from inference_api.usage import UsageService


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def _seed_unlimited_subscriber(session) -> User:
    await seed_default_billing_plans(session)
    plan = (
        await session.execute(select(BillingPlan).where(BillingPlan.slug == "unlimited"))
    ).scalar_one()
    assert plan.is_unlimited is True

    user = User(id=uuid.uuid4(), is_active=True, created_at=utcnow())
    session.add(user)
    await session.flush()

    now = utcnow()
    session.add(
        UserSubscription(
            id=uuid.uuid4(),
            user_id=user.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        )
    )
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_unlimited_subscriber_is_never_blocked_regardless_of_volume():
    async with async_session_maker() as session:
        user = await _seed_unlimited_subscriber(session)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="small-model",
            prompt_tokens=500_000_000,
            max_completion_tokens=500_000_000,
        )
        assert event.wallet_reserved_tokens == 0

        settled = await service.settle_inference_usage(
            event, prompt_tokens=500_000_000, completion_tokens=750_000_000
        )
        assert settled.charged_tokens == 1_250_000_000
        assert settled.wallet_reserved_tokens == 0

        second = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=2_000_000_000, max_completion_tokens=0
        )
        assert second.wallet_reserved_tokens == 0

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one_or_none()
        assert wallet is None or wallet.balance_tokens == 0
