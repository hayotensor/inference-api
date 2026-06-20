from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.billing_plan import BillingPlan


@dataclass(frozen=True)
class PlanSeed:
    slug: str
    name: str
    description: str
    monthly_token_allowance: int
    stripe_price_id: str | None
    visible: bool
    sort_order: int
    features: dict[str, Any]


def _stripe_price_id(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def default_plan_seeds() -> list[PlanSeed]:
    return [
        PlanSeed(
            slug="free",
            name="Free",
            description="Free monthly token allowance for verified developers.",
            monthly_token_allowance=settings.free_monthly_token_allowance,
            stripe_price_id=None,
            visible=True,
            sort_order=0,
            features={"support": "community", "included": ["API access"]},
        ),
        PlanSeed(
            slug="starter",
            name="Starter",
            description="Starter monthly token allowance for individual builders.",
            monthly_token_allowance=100_000,
            stripe_price_id=settings.stripe_starter_price_id,
            visible=True,
            sort_order=10,
            features={"support": "standard", "included": ["API access", "Usage dashboard"]},
        ),
        PlanSeed(
            slug="pro",
            name="Pro",
            description="Higher monthly token allowance for production workloads.",
            monthly_token_allowance=1_000_000,
            stripe_price_id=settings.stripe_pro_price_id,
            visible=True,
            sort_order=20,
            features={"support": "priority", "included": ["API access", "Usage dashboard"]},
        ),
        PlanSeed(
            slug="business",
            name="Business",
            description="Business monthly token allowance for teams.",
            monthly_token_allowance=10_000_000,
            stripe_price_id=settings.stripe_business_price_id,
            visible=True,
            sort_order=30,
            features={"support": "priority", "included": ["API access", "Usage dashboard"]},
        ),
    ]


async def seed_default_billing_plans(session: AsyncSession) -> None:
    for seed in default_plan_seeds():
        result = await session.execute(
            select(BillingPlan).where((BillingPlan.slug == seed.slug) | (BillingPlan.name == seed.name))
        )
        plan = result.scalar_one_or_none()
        stripe_price_id = _stripe_price_id(seed.stripe_price_id)
        if plan is None:
            session.add(
                BillingPlan(
                    slug=seed.slug,
                    name=seed.name,
                    description=seed.description,
                    stripe_price_id=stripe_price_id or None,
                    monthly_token_allowance=seed.monthly_token_allowance,
                    active=True,
                    visible=seed.visible,
                    sort_order=seed.sort_order,
                    features=seed.features,
                )
            )
            continue

        if not plan.slug:
            plan.slug = seed.slug
        if plan.description is None:
            plan.description = seed.description
        if plan.features is None:
            plan.features = seed.features
        if plan.sort_order == 0 and seed.sort_order:
            plan.sort_order = seed.sort_order
        if stripe_price_id and plan.stripe_price_id != stripe_price_id:
            plan.stripe_price_id = stripe_price_id
        session.add(plan)
    await session.flush()
