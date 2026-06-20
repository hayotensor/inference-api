from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
