"""HYP-36: multi-model usage formula (serving plane).

Mirrors ../../api/tests/test_hyp36_model_pricing.py against this plane's own
copy of the pricing machinery (`inference_api/pricing.py`,
`inference_api/usage.py`). Both planes independently gate quota (see the
repo's memory on the two-DBs footgun), so both must charge
`ceil(raw_tokens * weight[model])` against the allowance and must agree on the
seeded default weights, or a request could be admitted by one plane's
bookkeeping and refused by the other's for the same account.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.models import ManualTokenAdjustment, ModelPricing, User
from inference_api.pricing import DEFAULT_MODEL_PRICING, ModelPricingService, seed_default_model_pricing
from inference_api.security import utcnow
from inference_api.usage import UsageService, charged_token_count
from sqlalchemy import select


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    """Isolates the manual-adjustment allowance so the arithmetic below is exact,
    independent of whatever FREE_MONTHLY_TOKEN_ALLOWANCE a deployment's .env sets."""
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def _seed_user(session, *, allowance: int) -> User:
    user = User(id=uuid.uuid4(), is_active=True, created_at=utcnow())
    session.add(user)
    await session.flush()
    if allowance:
        session.add(
            ManualTokenAdjustment(
                id=uuid.uuid4(), user_id=user.id, amount=allowance, created_at=utcnow()
            )
        )
    await session.flush()
    return user


def test_default_catalog_matches_control_plane_weights():
    """Pins the weights this plane seeds by default -- must stay identical to
    ../../api/app/usage/pricing.py's DEFAULT_MODEL_PRICING (see module docstring)."""
    assert DEFAULT_MODEL_PRICING["small-model"] == Decimal("1")
    assert DEFAULT_MODEL_PRICING["medium-model"] == Decimal("2")
    assert DEFAULT_MODEL_PRICING["large-model"] == Decimal("5")


@pytest.mark.asyncio
async def test_weight_of_one_charges_raw_tokens_unchanged():
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
        assert event.reserved_tokens == 5_000

        settled = await service.settle_inference_usage(
            event, prompt_tokens=3_000, completion_tokens=1_500
        )
        assert settled.charged_tokens == 4_500

        period = await service.current_period(user)
        assert period.used_tokens == 4_500
        assert period.remaining_tokens == 100_000 - 4_500


@pytest.mark.asyncio
async def test_two_models_with_different_weights_drain_allowance_at_different_rates():
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

        cheap_period = await service.current_period(cheap_user)
        expensive_period = await service.current_period(expensive_user)
        assert cheap_period.used_tokens == 1_000
        assert expensive_period.used_tokens == 5_000
        assert cheap_period.remaining_tokens - expensive_period.remaining_tokens == 4_000


@pytest.mark.asyncio
async def test_settlement_rescales_by_the_models_weight():
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=100_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="medium-model",  # 2x
            prompt_tokens=1_000,
            max_completion_tokens=1_000,
        )
        assert event.reserved_tokens == 4_000

        settled = await service.settle_inference_usage(
            event, prompt_tokens=1_000, completion_tokens=500
        )
        assert settled.charged_tokens == charged_token_count(1_500, Decimal("2"))
        assert settled.charged_tokens == 3_000

        period = await service.current_period(user)
        assert period.used_tokens == 3_000


@pytest.mark.asyncio
async def test_inactive_model_pricing_falls_back_to_default_weight():
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
