"""HYP-22: production billing/pricing must never expose a test/e2e plan.

Verified in prod on 2026-08-27: `GET /billing/plans` returned a visible, active
`slug=e2e-test-plan` / `stripe_price_id=price_fake_e2e_123` row. The row itself
already had `active=True, visible=True` set directly, so the pre-existing
`active AND visible` filter in `BillingService.list_plans` could not have caught
it -- however that row got created (most likely: an e2e suite POSTing to
`/admin/billing/plans` against a real/production database and never cleaning
up, which is a process bug, not just a code one; see the report for this
ticket). These tests cover the two code-level defenses added for it:

1. `GET /billing/plans` (and the checkout/plan-change path) filters out anything
   that *looks* like a test/e2e plan -- by slug/name or by a `price_fake_*`
   Stripe price ID -- regardless of what its `active`/`visible` columns say, so
   a leftover DB row like the one found in prod can never leak again.
2. The admin billing-plan endpoints (`create`, `update`, `activate`) can no
   longer produce such a plan as active/visible in the first place, even if the
   caller explicitly asks for that.
"""

from __future__ import annotations

import uuid

from app.admin.permissions import OWNER_ROLE
from app.billing.plans import looks_like_test_plan
from app.db.models.billing_plan import BillingPlan
from app.db.session import async_session_maker

from tests.test_admin import assign_role, register_and_login


async def _owner_token(client) -> str:
    login = await register_and_login(client, "owner@example.com")
    await assign_role("owner@example.com", OWNER_ROLE)
    return login["access_token"]


async def _insert_plan_directly(**overrides) -> BillingPlan:
    """Bypasses the API entirely -- simulates a row that ended up in the
    database some other way (the prod leak's actual, unknown origin)."""
    defaults = dict(
        id=uuid.uuid4(),
        slug="e2e-test-plan",
        name="E2E Test Plan (patched)",
        description="temp plan for e2e smoke test",
        stripe_price_id="price_fake_e2e_123",
        price_cents=0,
        monthly_token_allowance=75_000,
        active=True,
        visible=True,
    )
    defaults.update(overrides)
    plan = BillingPlan(**defaults)
    async with async_session_maker() as session:
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
    return plan


# --------------------------------------------------------------------------- #
# Pure predicate
# --------------------------------------------------------------------------- #


def test_looks_like_test_plan_matches_the_prod_leak_shape():
    assert looks_like_test_plan(
        slug="e2e-test-plan",
        name="E2E Test Plan (patched)",
        stripe_price_id="price_fake_e2e_123",
    )


def test_looks_like_test_plan_matches_on_slug_alone():
    assert looks_like_test_plan(slug="internal-test", name="Internal", stripe_price_id="price_1Abc")


def test_looks_like_test_plan_matches_on_fake_price_id_alone():
    assert looks_like_test_plan(slug="promo", name="Promo", stripe_price_id="price_fake_anything")


def test_looks_like_test_plan_does_not_flag_real_plans():
    for slug, name in [("free", "Free"), ("starter", "Starter"), ("pro", "Pro"), ("business", "Business")]:
        assert not looks_like_test_plan(slug=slug, name=name, stripe_price_id="price_1RealStripeId")


def test_looks_like_test_plan_does_not_flag_real_price_test_fixtures():
    # Existing tests use `price_test_*` fixture IDs for *real* plans (starter/pro/
    # business) pointed at a Stripe test-mode account -- only the `price_fake_*`
    # prefix (a placeholder never created in Stripe at all) should be flagged.
    assert not looks_like_test_plan(
        slug="starter", name="Starter", stripe_price_id="price_test_starter_month"
    )


# --------------------------------------------------------------------------- #
# GET /billing/plans (public catalog) — the actual leak surface
# --------------------------------------------------------------------------- #


async def test_leftover_active_visible_test_plan_is_never_returned(client):
    await _insert_plan_directly()

    response = await client.get("/billing/plans")
    assert response.status_code == 200, response.text
    plans = response.json()

    assert not any("e2e" in plan["slug"].lower() or "test" in plan["slug"].lower() for plan in plans)
    assert not any(
        (plan.get("stripe_price_id") or "").startswith("price_fake") for plan in plans
    )
    assert not any("E2E Test Plan" in plan["name"] for plan in plans)
    # The real catalog must still be there and unaffected.
    assert {"free", "starter", "pro", "business"} <= {plan["slug"] for plan in plans}


async def test_real_plans_are_unaffected_by_the_filter(client):
    response = await client.get("/billing/plans")
    assert response.status_code == 200, response.text
    slugs = {plan["slug"] for plan in response.json()}
    assert {"free", "starter", "pro", "business"} <= slugs


async def test_checkout_refuses_a_leftover_test_plan_even_if_active_and_visible(client):
    plan = await _insert_plan_directly()
    login = await register_and_login(client, "shopper@example.com")
    token = login["access_token"]

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": str(plan.id)},
    )
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# Admin endpoints can no longer *produce* an active/visible test plan
# --------------------------------------------------------------------------- #


async def test_admin_create_forces_test_looking_plan_hidden_and_inactive(client):
    token = await _owner_token(client)

    created = await client.post(
        "/admin/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "e2e-test-plan",
            "name": "E2E Test Plan (patched)",
            "description": "temp plan for e2e smoke test",
            "stripe_price_id": "price_fake_e2e_123",
            "monthly_token_allowance": 75_000,
            "active": True,
            "visible": True,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["active"] is False
    assert body["visible"] is False

    # And it must not show up in the public catalog either.
    public = await client.get("/billing/plans")
    assert not any(plan["slug"] == "e2e-test-plan" for plan in public.json())


async def test_admin_create_leaves_normal_plans_untouched(client):
    token = await _owner_token(client)

    created = await client.post(
        "/admin/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "slug": "enterprise",
            "name": "Enterprise",
            "monthly_token_allowance": 50_000_000,
            "active": True,
            "visible": True,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["active"] is True
    assert created.json()["visible"] is True


async def test_admin_update_forces_test_looking_plan_hidden_and_inactive(client):
    token = await _owner_token(client)
    created = await client.post(
        "/admin/billing/plans",
        headers={"Authorization": f"Bearer {token}"},
        json={"slug": "promo", "name": "Promo", "monthly_token_allowance": 10_000},
    )
    plan_id = created.json()["id"]

    updated = await client.patch(
        f"/admin/billing/plans/{plan_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"stripe_price_id": "price_fake_oops", "active": True, "visible": True},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["active"] is False
    assert updated.json()["visible"] is False


async def test_admin_cannot_activate_a_test_looking_plan(client):
    token = await _owner_token(client)
    plan = await _insert_plan_directly(active=False, visible=False)

    activated = await client.post(
        f"/admin/billing/plans/{plan.id}/activate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert activated.status_code == 400, activated.text

    async with async_session_maker() as session:
        refreshed = await session.get(BillingPlan, plan.id)
        assert refreshed.active is False
        assert refreshed.visible is False
