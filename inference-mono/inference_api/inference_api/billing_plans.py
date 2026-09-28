from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.models import BillingPlan


@dataclass(frozen=True)
class PlanSeed:
    slug: str
    name: str
    description: str
    monthly_token_allowance: int
    visible: bool
    sort_order: int
    features: dict[str, Any]
    is_unlimited: bool = False


def default_plan_seeds() -> list[PlanSeed]:
    return [
        PlanSeed(
            slug="free",
            name="Free",
            description="Free monthly token allowance for verified developers.",
            monthly_token_allowance=settings.free_monthly_token_allowance,
            visible=True,
            sort_order=0,
            features={"support": "community", "included": ["API access"]},
        ),
        PlanSeed(
            slug="starter",
            name="Starter",
            description="Starter monthly token allowance for individual builders.",
            monthly_token_allowance=100_000,
            visible=True,
            sort_order=10,
            features={"support": "standard", "included": ["API access", "Usage dashboard"]},
        ),
        PlanSeed(
            slug="pro",
            name="Pro",
            description="Higher monthly token allowance for production workloads.",
            monthly_token_allowance=1_000_000,
            visible=True,
            sort_order=20,
            features={"support": "priority", "included": ["API access", "Usage dashboard"]},
        ),
        PlanSeed(
            slug="business",
            name="Business",
            description="Business monthly token allowance for teams.",
            monthly_token_allowance=10_000_000,
            visible=True,
            sort_order=30,
            features={"support": "priority", "included": ["API access", "Usage dashboard"]},
        ),
        # HYP-76: mirrors ../../api/app/billing/plans.py's "unlimited" seed. This
        # plane never creates the plan row (the control plane's checkout flow
        # does, and Stripe pricing is meaningless here) -- this entry exists so
        # a fresh/test DB seeded from *this* plane's seeder still gets an
        # is_unlimited=True row to enforce against.
        PlanSeed(
            slug="unlimited",
            name="Unlimited",
            description="Unlimited inference for the highest, least predictable volume.",
            monthly_token_allowance=0,
            visible=True,
            sort_order=40,
            features={"support": "priority", "included": ["API access", "Usage dashboard", "Unlimited tokens"]},
            is_unlimited=True,
        ),
    ]


async def seed_default_billing_plans(session: AsyncSession) -> None:
    for seed in default_plan_seeds():
        result = await session.execute(
            select(BillingPlan).where((BillingPlan.slug == seed.slug) | (BillingPlan.name == seed.name))
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            session.add(
                BillingPlan(
                    slug=seed.slug,
                    name=seed.name,
                    description=seed.description,
                    monthly_token_allowance=seed.monthly_token_allowance,
                    is_unlimited=seed.is_unlimited,
                    active=True,
                    visible=seed.visible,
                    sort_order=seed.sort_order,
                    features=seed.features,
                )
            )
            continue
        if plan.is_unlimited != seed.is_unlimited:
            plan.is_unlimited = seed.is_unlimited
        if not plan.slug:
            plan.slug = seed.slug
        if plan.description is None:
            plan.description = seed.description
        if plan.features is None:
            plan.features = seed.features
        if plan.sort_order == 0 and seed.sort_order:
            plan.sort_order = seed.sort_order
        session.add(plan)
    await session.flush()
