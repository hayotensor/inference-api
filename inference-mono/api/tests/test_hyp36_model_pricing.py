"""HYP-36: multi-model usage formula (control plane).

Each model carries a per-token cost-weight (`model_pricing.token_multiplier`)
relative to a baseline of 1.0. A plan's token allowance is denominated in
baseline-equivalent tokens, and usage against model M consumes
`ceil(raw_tokens * weight[M])` of that allowance instead of the raw token count
(see `charged_token_count` / `ModelPricingService` in `app/usage/service.py` and
`app/usage/pricing.py`). This lets a cheap model stretch the allowance further
and an expensive one draw it down faster, while a weight of 1.0 (the default,
and what every model gets until an admin sets otherwise) reproduces the
pre-HYP-36 behavior exactly -- raw tokens charged 1:1.

These tests exercise `UsageService` directly against the seeded default catalog
in `app/usage/pricing.py` (`small-model` -> 1x, `medium-model` -> 2x,
`large-model` -> 5x), so they also pin those defaults and catch drift between
this plane's copy of the catalog and the serving plane's
(`inference_api/inference_api/pricing.py`), which must agree per the two-DBs
footgun documented in the repo's memory.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import utcnow
from app.db.models.manual_token_adjustment import ManualTokenAdjustment
from app.db.models.model_pricing import ModelPricing
from app.db.models.user import User
from app.db.session import async_session_maker
from app.usage.pricing import DEFAULT_MODEL_PRICING, ModelPricingService, seed_default_model_pricing
from app.usage.service import UsageService, charged_token_count


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    """These tests isolate the manual-adjustment allowance so the arithmetic is
    exact; a deployment's real FREE_MONTHLY_TOKEN_ALLOWANCE (loaded from .env)
    would otherwise add an untracked amount on top of it."""
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def _seed_user(session, *, allowance: int) -> User:
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
    if allowance:
        session.add(
            ManualTokenAdjustment(
                id=uuid.uuid4(),
                user_id=user.id,
                amount=allowance,
                reason="hyp-36 test allowance",
                created_at=utcnow(),
            )
        )
    await session.flush()
    return user


def test_default_catalog_pins_expected_weights():
    """Pins the seeded weights this whole test module relies on, and documents
    the formula's baseline: every model defaults to 1.0 unless configured
    otherwise, so adding a new model never silently changes existing bills."""
    assert DEFAULT_MODEL_PRICING["small-model"] == Decimal("1")
    assert DEFAULT_MODEL_PRICING["medium-model"] == Decimal("2")
    assert DEFAULT_MODEL_PRICING["large-model"] == Decimal("5")


@pytest.mark.asyncio
async def test_weight_of_one_charges_raw_tokens_unchanged():
    """A model at the 1.0 baseline weight must charge exactly the raw token
    count -- this is the "existing single-model behavior is unchanged" guarantee
    from HYP-36: today's bills must not move for any model left at the default."""
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=100_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="small-model",
            prompt_tokens=3_000,
            max_completion_tokens=2_000,
        )
        assert event.model_multiplier == Decimal("1")
        assert event.reserved_tokens == 5_000  # raw tokens, unscaled

        settled = await service.settle_inference_usage(
            event, prompt_tokens=3_000, completion_tokens=1_500
        )
        assert settled.charged_tokens == 4_500  # still raw, 1:1

        period = await service.current_period(user)
        assert period.used_tokens == 4_500
        assert period.remaining_tokens == 100_000 - 4_500


@pytest.mark.asyncio
async def test_two_models_with_different_weights_drain_allowance_at_different_rates():
    """Same raw token count against two models with different weights must
    consume the shared allowance at different rates -- the core of the HYP-36
    formula: usage against model M consumes `raw_tokens * weight[M]`."""
    raw_tokens_each = 1_000

    async with async_session_maker() as session:
        cheap_user = await _seed_user(session, allowance=1_000_000)
        expensive_user = await _seed_user(session, allowance=1_000_000)
        service = UsageService(session)

        cheap_event = await service.reserve_inference_tokens(
            cheap_user,
            api_key_id=uuid.uuid4(),
            model="small-model",  # 1x
            prompt_tokens=raw_tokens_each,
            max_completion_tokens=0,
        )
        expensive_event = await service.reserve_inference_tokens(
            expensive_user,
            api_key_id=uuid.uuid4(),
            model="large-model",  # 5x
            prompt_tokens=raw_tokens_each,
            max_completion_tokens=0,
        )

        assert cheap_event.reserved_tokens == raw_tokens_each * 1
        assert expensive_event.reserved_tokens == raw_tokens_each * 5
        assert expensive_event.reserved_tokens == cheap_event.reserved_tokens * 5

        cheap_period = await service.current_period(cheap_user)
        expensive_period = await service.current_period(expensive_user)
        assert cheap_period.used_tokens == 1_000
        assert expensive_period.used_tokens == 5_000
        # Same raw usage, same starting allowance -> the expensive model leaves
        # the account with less allowance remaining.
        assert expensive_period.remaining_tokens < cheap_period.remaining_tokens
        assert cheap_period.remaining_tokens - expensive_period.remaining_tokens == 4_000


@pytest.mark.asyncio
async def test_settlement_rescales_by_the_models_weight_not_the_reservation_estimate():
    """Settlement must reapply the model's weight to the *actual* token count,
    not just adjust the raw delta -- otherwise a model change or a rounding
    mismatch between reserve/settle could under- or over-charge the allowance."""
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=100_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="medium-model",  # 2x
            prompt_tokens=1_000,
            max_completion_tokens=1_000,  # estimate: 2000 raw -> 4000 reserved
        )
        assert event.reserved_tokens == 4_000

        settled = await service.settle_inference_usage(
            event, prompt_tokens=1_000, completion_tokens=500  # actual: 1500 raw
        )
        assert settled.charged_tokens == charged_token_count(1_500, Decimal("2"))
        assert settled.charged_tokens == 3_000

        period = await service.current_period(user)
        assert period.used_tokens == 3_000  # reservation's excess was released back


@pytest.mark.asyncio
async def test_inactive_model_pricing_falls_back_to_default_weight():
    """A deactivated ModelPricing row must not silently zero out or otherwise
    corrupt charging -- it should fall back to the 1.0 baseline, same as an
    unknown model, so disabling a stale pricing row can never make a model free."""
    async with async_session_maker() as session:
        await seed_default_model_pricing(session)
        result = await session.execute(
            select(ModelPricing).where(ModelPricing.model_name == "large-model")
        )
        pricing = result.scalar_one()
        pricing.active = False
        session.add(pricing)
        await session.flush()

        multiplier = await ModelPricingService(session).multiplier_for_model("large-model")
        assert multiplier == Decimal("1")


@pytest.mark.asyncio
async def test_unknown_model_defaults_to_baseline_weight():
    async with async_session_maker() as session:
        multiplier = await ModelPricingService(session).multiplier_for_model(
            "a-model-nobody-configured-pricing-for"
        )
        assert multiplier == Decimal("1")
