from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.billing_plan import BillingPlan

# Annual prices are billed as 10x the monthly price (two months free).
ANNUAL_MONTHS_BILLED = 10


@dataclass(frozen=True)
class PlanSeed:
    slug: str
    name: str
    description: str
    monthly_token_allowance: int
    price_cents: int
    stripe_price_id: str | None
    annual_price_cents: int | None
    stripe_annual_price_id: str | None
    currency: str
    visible: bool
    sort_order: int
    features: dict[str, Any]


def _stripe_price_id(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _annual_cents(monthly_cents: int) -> int | None:
    if monthly_cents <= 0:
        return None
    return monthly_cents * ANNUAL_MONTHS_BILLED


def default_plan_seeds() -> list[PlanSeed]:
    """The plan catalog. This is the source of truth for `scripts/stripe_sync_plans.py`,
    which creates the matching Stripe products/prices and prints the price-ID env lines."""
    return [
        PlanSeed(
            slug="free",
            name="Free",
            description="Free monthly token allowance for verified developers.",
            monthly_token_allowance=settings.free_monthly_token_allowance,
            price_cents=0,
            stripe_price_id=None,
            annual_price_cents=None,
            stripe_annual_price_id=None,
            currency="usd",
            visible=True,
            sort_order=0,
            features={"support": "community", "included": ["API access"]},
        ),
        PlanSeed(
            slug="starter",
            name="Starter",
            description="Starter monthly token allowance for individual builders.",
            monthly_token_allowance=1_000_000,
            price_cents=900,
            stripe_price_id=settings.stripe_starter_price_id,
            annual_price_cents=_annual_cents(900),
            stripe_annual_price_id=settings.stripe_starter_annual_price_id,
            currency="usd",
            visible=True,
            sort_order=10,
            features={"support": "standard", "included": ["API access", "Usage dashboard"]},
        ),
        PlanSeed(
            slug="pro",
            name="Pro",
            description="Higher monthly token allowance for production workloads.",
            monthly_token_allowance=10_000_000,
            price_cents=4900,
            stripe_price_id=settings.stripe_pro_price_id,
            annual_price_cents=_annual_cents(4900),
            stripe_annual_price_id=settings.stripe_pro_annual_price_id,
            currency="usd",
            visible=True,
            sort_order=20,
            features={"support": "priority", "included": ["API access", "Usage dashboard"]},
        ),
        PlanSeed(
            slug="business",
            name="Business",
            description="Business monthly token allowance for teams.",
            monthly_token_allowance=100_000_000,
            price_cents=29900,
            stripe_price_id=settings.stripe_business_price_id,
            annual_price_cents=_annual_cents(29900),
            stripe_annual_price_id=settings.stripe_business_annual_price_id,
            currency="usd",
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
        stripe_annual_price_id = _stripe_price_id(seed.stripe_annual_price_id)
        if plan is None:
            session.add(
                BillingPlan(
                    slug=seed.slug,
                    name=seed.name,
                    description=seed.description,
                    stripe_price_id=stripe_price_id or None,
                    stripe_annual_price_id=stripe_annual_price_id or None,
                    price_cents=seed.price_cents,
                    annual_price_cents=seed.annual_price_cents if stripe_annual_price_id else None,
                    currency=seed.currency,
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
        if not plan.currency:
            plan.currency = seed.currency
        if not plan.monthly_token_allowance and seed.monthly_token_allowance and not seed.price_cents:
            # Free plan created while FREE_MONTHLY_TOKEN_ALLOWANCE was still 0.
            plan.monthly_token_allowance = seed.monthly_token_allowance
        # The env price IDs win over stored ones; the displayed amount follows the price
        # we point at so the UI can never advertise a number Stripe would not charge.
        if stripe_price_id and plan.stripe_price_id != stripe_price_id:
            plan.stripe_price_id = stripe_price_id
            plan.price_cents = seed.price_cents
        if stripe_annual_price_id and plan.stripe_annual_price_id != stripe_annual_price_id:
            plan.stripe_annual_price_id = stripe_annual_price_id
            plan.annual_price_cents = seed.annual_price_cents
        # Backfill amounts for rows created before prices were tracked, without
        # clobbering an amount an admin has since set by hand.
        if not plan.price_cents and plan.stripe_price_id and seed.price_cents:
            plan.price_cents = seed.price_cents
        if not plan.annual_price_cents and plan.stripe_annual_price_id and seed.annual_price_cents:
            plan.annual_price_cents = seed.annual_price_cents
        # A plan with no annual price must not advertise an annual amount.
        if not plan.stripe_annual_price_id and plan.annual_price_cents:
            plan.annual_price_cents = None
        session.add(plan)
    await session.flush()
