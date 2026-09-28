from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.models import ModelPricing

DEFAULT_MODEL_PRICING = {
    "demo-inference-001": Decimal("1"),
    "demo-chat-001": Decimal("1"),
    "small-model": Decimal("1"),
    "medium-model": Decimal("2"),
    "large-model": Decimal("5"),
}


async def seed_default_model_pricing(session: AsyncSession) -> None:
    for model_name, multiplier in DEFAULT_MODEL_PRICING.items():
        result = await session.execute(select(ModelPricing).where(ModelPricing.model_name == model_name))
        if result.scalar_one_or_none() is None:
            session.add(
                ModelPricing(
                    model_name=model_name,
                    token_multiplier=multiplier,
                    active=True,
                )
            )
    await session.flush()


class ModelPricingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def multiplier_for_model(self, model_name: str) -> Decimal:
        await seed_default_model_pricing(self.session)
        result = await self.session.execute(
            select(ModelPricing).where(
                ModelPricing.model_name == model_name,
                ModelPricing.active.is_(True),
            )
        )
        pricing = result.scalar_one_or_none()
        if pricing is None:
            return Decimal("1")
        return Decimal(pricing.token_multiplier)


# The demo model seeded into model_allowlist (inference_api.allowlist_seed) and
# used pervasively as the default test/dev model -- the only sane fallback if
# model_pricing somehow has zero active rows (a brand-new, unseeded database).
_GUEST_MODEL_FALLBACK = "demo-chat-001"


async def resolve_guest_model_id(session: AsyncSession) -> str:
    """The single model anonymous/guest requests (HYP-67) are always forced onto.

    ``settings.guest_model_id`` is an explicit operator override -- set it to
    pin the sandbox to one model regardless of pricing. Left unset (the
    default), this resolves to whichever ACTIVE ``model_pricing`` row has the
    lowest ``token_multiplier`` (ties broken by ``model_name`` for
    determinism), so the sandbox always serves the platform's cheapest priced
    model without a second hardcoded constant that could silently drift from
    the pricing table.
    """
    if settings.guest_model_id:
        return settings.guest_model_id
    await seed_default_model_pricing(session)
    result = await session.execute(
        select(ModelPricing.model_name)
        .where(ModelPricing.active.is_(True))
        .order_by(ModelPricing.token_multiplier.asc(), ModelPricing.model_name.asc())
        .limit(1)
    )
    model_name = result.scalar_one_or_none()
    return model_name or _GUEST_MODEL_FALLBACK
