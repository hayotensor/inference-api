"""Stripe billing: real prices on plans, and interval-aware checkout.

Plans used to carry only a Stripe price ID and no amount, so every surface that
tried to show a price showed "$0". These tests pin the money-carrying fields, the
monthly/annual price selection at checkout, and the failure modes when Stripe is
not configured.
"""

import uuid

import pytest

from app.billing.service import BillingService
from app.core.config import settings
from app.db.models.billing_plan import BillingPlan
from app.db.session import async_session_maker

MONTHLY_IDS = {
    "starter": "price_test_starter_month",
    "pro": "price_test_pro_month",
    "business": "price_test_business_month",
}
ANNUAL_IDS = {
    "starter": "price_test_starter_year",
    "pro": "price_test_pro_year",
    "business": "price_test_business_year",
}


class FakeStripeClient:
    """Records what the service asked Stripe to do."""

    calls: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def create_customer(self, *, email: str, name: str | None, user_id: str) -> str:
        self.calls.append(("create_customer", {"email": email, "user_id": user_id}))
        return "cus_test_123"

    def create_checkout_session(self, *, customer_id, price_id, user_id, plan_id):
        self.calls.append(
            (
                "create_checkout_session",
                {"customer_id": customer_id, "price_id": price_id, "plan_id": plan_id},
            )
        )
        return {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay/cs_test_123"}

    def create_customer_portal_session(self, *, customer_id):
        self.calls.append(("create_customer_portal_session", {"customer_id": customer_id}))
        return {"id": "bps_test_123", "url": "https://billing.stripe.com/p/session/bps_test_123"}

    def cancel_subscription(self, *, subscription_id, cancel_at_period_end):
        raise AssertionError("not used in these tests")

    def retrieve_subscription(self, subscription_id):
        raise AssertionError("not used in these tests")

    def retrieve_checkout_session(self, session_id):
        raise AssertionError("not used in these tests")


@pytest.fixture
def fake_stripe(monkeypatch):
    FakeStripeClient.calls = []
    monkeypatch.setattr("app.billing.service.StripeClient", FakeStripeClient)
    return FakeStripeClient


@pytest.fixture
def stripe_prices(monkeypatch):
    """Point the plan catalog at fake price IDs, as a configured deployment would."""
    for slug, price_id in MONTHLY_IDS.items():
        monkeypatch.setattr(settings, f"stripe_{slug}_price_id", price_id)
    for slug, price_id in ANNUAL_IDS.items():
        monkeypatch.setattr(settings, f"stripe_{slug}_annual_price_id", price_id)


@pytest.fixture
def monthly_prices_only(monkeypatch):
    """Monthly price IDs configured, annual explicitly absent. Both halves are set
    deliberately: a real .env with annual IDs would otherwise leak into the test."""
    for slug, price_id in MONTHLY_IDS.items():
        monkeypatch.setattr(settings, f"stripe_{slug}_price_id", price_id)
    for slug in ANNUAL_IDS:
        monkeypatch.setattr(settings, f"stripe_{slug}_annual_price_id", None)


@pytest.fixture
def verified_signup(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", True)


async def _login(client, email: str = "billing@example.com") -> tuple[str, str]:
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": "B1lling-Passw0rd!", "full_name": "Billing User"},
    )
    assert register.status_code == 201, register.text
    user_id = register.json()["id"]
    login = await client.post(
        "/auth/login", json={"email": email, "password": "B1lling-Passw0rd!"}
    )
    assert login.status_code == 200, login.text
    return user_id, login.json()["access_token"]


async def _plans(client) -> dict[str, dict]:
    response = await client.get("/billing/plans")
    assert response.status_code == 200, response.text
    return {plan["slug"]: plan for plan in response.json()}


async def test_paid_plans_expose_a_real_price(client, stripe_prices):
    plans = await _plans(client)

    assert plans["starter"]["price_cents"] == 900
    assert plans["starter"]["annual_price_cents"] == 9000
    assert plans["pro"]["price_cents"] == 4900
    assert plans["pro"]["annual_price_cents"] == 49000
    assert plans["business"]["price_cents"] == 29900
    assert plans["business"]["annual_price_cents"] == 299000
    assert {plan["currency"] for plan in plans.values()} == {"usd"}
    # Allowances must match the published ladder, or the price per token is a lie.
    assert plans["starter"]["monthly_token_allowance"] == 1_000_000
    assert plans["pro"]["monthly_token_allowance"] == 10_000_000
    assert plans["business"]["monthly_token_allowance"] == 100_000_000


async def test_free_plan_stays_free_and_unpurchasable(client, stripe_prices):
    free = (await _plans(client))["free"]

    assert free["price_cents"] == 0
    assert free["annual_price_cents"] is None
    assert free["stripe_price_id"] is None
    assert free["stripe_annual_price_id"] is None


async def test_plan_never_advertises_an_annual_amount_without_an_annual_price(
    client, monthly_prices_only
):
    plans = await _plans(client)
    for slug in MONTHLY_IDS:
        assert plans[slug]["price_cents"] > 0
        assert plans[slug]["stripe_annual_price_id"] is None
        assert plans[slug]["annual_price_cents"] is None


async def test_checkout_defaults_to_the_monthly_price(client, stripe_prices, fake_stripe, verified_signup):
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://checkout.stripe.com/")

    checkout = dict(fake_stripe.calls)["create_checkout_session"]
    assert checkout["price_id"] == MONTHLY_IDS["pro"]


async def test_checkout_uses_the_annual_price_when_asked(client, stripe_prices, fake_stripe, verified_signup):
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"], "interval": "year"},
    )
    assert response.status_code == 200, response.text

    checkout = dict(fake_stripe.calls)["create_checkout_session"]
    assert checkout["price_id"] == ANNUAL_IDS["pro"]


async def test_checkout_rejects_an_interval_the_plan_has_no_price_for(
    client, fake_stripe, verified_signup, monthly_prices_only
):
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["starter"]["id"], "interval": "year"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "stripe_price_missing"


async def test_checkout_rejects_an_unknown_interval(client, stripe_prices, fake_stripe, verified_signup):
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"], "interval": "decade"},
    )
    assert response.status_code == 422, response.text


async def test_checkout_on_the_free_plan_is_rejected(client, stripe_prices, fake_stripe, verified_signup):
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["free"]["id"]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "stripe_price_missing"


async def test_checkout_without_a_secret_key_is_service_unavailable(
    client, stripe_prices, verified_signup, monkeypatch
):
    """No fake client here: the real StripeClient must refuse without a key."""
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "stripe_not_configured"


async def test_subscription_reports_the_annual_amount_and_interval(
    client, stripe_prices, fake_stripe, verified_signup
):
    user_id, token = await _login(client)
    plans = await _plans(client)

    # What the webhook writes after an annual checkout completes.
    async with async_session_maker() as session:
        await BillingService(session).sync_subscription_object(
            {
                "id": "sub_test_annual",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id, "plan_id": plans["pro"]["id"]},
                "items": {"data": [{"price": {"id": ANNUAL_IDS["pro"]}}]},
                "cancel_at_period_end": False,
            }
        )
        await session.commit()

    response = await client.get(
        "/billing/subscription", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["plan_name"] == "Pro"
    assert body["interval"] == "year"
    assert body["amount_cents"] == 49000
    assert body["currency"] == "usd"


async def test_subscription_reports_the_monthly_amount(
    client, stripe_prices, fake_stripe, verified_signup
):
    user_id, token = await _login(client)
    plans = await _plans(client)

    async with async_session_maker() as session:
        await BillingService(session).sync_subscription_object(
            {
                "id": "sub_test_monthly",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id, "plan_id": plans["starter"]["id"]},
                "items": {"data": [{"price": {"id": MONTHLY_IDS["starter"]}}]},
                "cancel_at_period_end": False,
            }
        )
        await session.commit()

    body = (
        await client.get("/billing/subscription", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert body["interval"] == "month"
    assert body["amount_cents"] == 900


async def test_annual_price_id_resolves_back_to_its_plan(client, stripe_prices, fake_stripe, verified_signup):
    """A webhook that carries no plan_id metadata must still find the plan by price."""
    user_id, _ = await _login(client)

    async with async_session_maker() as session:
        subscription = await BillingService(session).sync_subscription_object(
            {
                "id": "sub_test_no_metadata",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id},
                "items": {"data": [{"price": {"id": ANNUAL_IDS["business"]}}]},
                "cancel_at_period_end": False,
            }
        )
        await session.commit()
        assert subscription is not None
        plan = await session.get(BillingPlan, subscription.plan_id)
        assert plan is not None and plan.slug == "business"


async def test_an_active_subscriber_cannot_start_a_second_checkout(
    client, stripe_prices, fake_stripe, verified_signup
):
    """They change plans through /billing/subscription/change; the portal stays
    available for invoices and payment methods."""
    user_id, token = await _login(client)
    plans = await _plans(client)
    async with async_session_maker() as session:
        await BillingService(session).sync_subscription_object(
            {
                "id": "sub_test_active",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id, "plan_id": plans["starter"]["id"]},
                "items": {"data": [{"price": {"id": MONTHLY_IDS["starter"]}}]},
                "cancel_at_period_end": False,
            }
        )
        await session.commit()

    response = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "subscription_exists"

    portal = await client.post(
        "/billing/stripe/customer-portal", headers={"Authorization": f"Bearer {token}"}
    )
    assert portal.status_code == 200, portal.text
    assert portal.json()["url"].startswith("https://billing.stripe.com/")


async def test_second_checkout_is_refused_while_the_first_is_still_open(
    client, stripe_prices, fake_stripe, verified_signup, monkeypatch
):
    """Nothing in our own DB is 'subscribed' yet at this point — the webhook has
    not fired — so the old guard (checking for a locally synced active
    subscription) would let a double-click or a second tab open a second Checkout
    Session and get the customer billed twice."""
    _, token = await _login(client)
    plans = await _plans(client)

    monkeypatch.setattr(
        FakeStripeClient,
        "retrieve_checkout_session",
        lambda self, session_id: {"id": session_id, "status": "open"},
    )

    first = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert second.status_code == 400, second.text
    assert second.json()["detail"]["code"] == "checkout_session_pending"

    calls = [name for name, _ in fake_stripe.calls]
    assert calls.count("create_checkout_session") == 1


async def test_a_new_checkout_is_allowed_once_the_first_session_resolves(
    client, stripe_prices, fake_stripe, verified_signup, monkeypatch
):
    """Once Stripe reports the earlier session as complete or expired, the pending
    marker should not block anything further (the completed case also confirms
    checkout normally clears it as part of syncing the subscription)."""
    _, token = await _login(client)
    plans = await _plans(client)

    monkeypatch.setattr(
        FakeStripeClient,
        "retrieve_checkout_session",
        lambda self, session_id: {"id": session_id, "status": "expired"},
    )

    first = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert second.status_code == 200, second.text

    calls = [name for name, _ in fake_stripe.calls]
    assert calls.count("create_checkout_session") == 2


async def test_sync_checkout_session_activates_the_subscription_without_the_webhook(
    client, stripe_prices, fake_stripe, verified_signup, monkeypatch
):
    """/billing/success calls this right after the Stripe redirect so the new plan
    shows up immediately, instead of the page depending entirely on webhook
    delivery timing."""
    user_id, token = await _login(client)
    plans = await _plans(client)

    checkout = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert checkout.status_code == 200, checkout.text
    session_id = checkout.json()["id"]

    monkeypatch.setattr(
        FakeStripeClient,
        "retrieve_checkout_session",
        lambda self, sid: {
            "id": sid,
            "customer": "cus_test_123",
            "client_reference_id": user_id,
            "metadata": {"user_id": user_id, "plan_id": plans["pro"]["id"]},
            "subscription": {
                "id": "sub_test_synced",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id, "plan_id": plans["pro"]["id"]},
                "items": {"data": [{"price": {"id": MONTHLY_IDS["pro"]}}]},
                "cancel_at_period_end": False,
            },
        },
    )

    synced = await client.post(
        "/billing/stripe/checkout-session/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"session_id": session_id},
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["status"] == "active"
    assert synced.json()["plan_name"] == "Pro"

    # A pending checkout no longer blocks a fresh one for a *different* plan once
    # this subscription is active — the ordinary "already subscribed" guard takes
    # over from the pending-session guard at that point.
    again = await client.post(
        "/billing/stripe/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["business"]["id"]},
    )
    assert again.status_code == 400, again.text
    assert again.json()["detail"]["code"] == "subscription_exists"


async def test_sync_checkout_session_rejects_another_users_session(
    client, stripe_prices, fake_stripe, verified_signup, monkeypatch
):
    _, token_a = await _login(client, email="checkout-a@example.com")
    other_user_id, _ = await _login(client, email="checkout-b@example.com")
    plans = await _plans(client)

    monkeypatch.setattr(
        FakeStripeClient,
        "retrieve_checkout_session",
        lambda self, sid: {
            "id": sid,
            "customer": "cus_test_other",
            "client_reference_id": other_user_id,
            "metadata": {"user_id": other_user_id, "plan_id": plans["pro"]["id"]},
        },
    )

    response = await client.post(
        "/billing/stripe/checkout-session/sync",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"session_id": "cs_test_someone_elses"},
    )
    assert response.status_code == 403, response.text


async def test_portal_session_passes_the_configured_configuration_id(monkeypatch):
    """A Stripe account with no dashboard-configured portal needs an explicit
    configuration, or billing_portal.Session.create fails."""
    from app.billing.stripe import StripeClient

    captured: dict = {}

    class FakePortalSession:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return {"id": "bps_1", "url": "https://billing.stripe.com/p/session/bps_1"}

    class FakeStripeModule:
        billing_portal = type("BillingPortal", (), {"Session": FakePortalSession})

    monkeypatch.setattr(StripeClient, "_stripe", lambda self: FakeStripeModule)
    monkeypatch.setattr(settings, "stripe_portal_configuration_id", "bpc_test_1")
    StripeClient().create_customer_portal_session(customer_id="cus_1")
    assert captured["configuration"] == "bpc_test_1"

    captured.clear()
    monkeypatch.setattr(settings, "stripe_portal_configuration_id", None)
    StripeClient().create_customer_portal_session(customer_id="cus_1")
    assert "configuration" not in captured


async def test_plan_ids_are_stable_across_reseeds(client, stripe_prices):
    """Seeding runs on every /billing/plans call; it must not duplicate plans."""
    first = await _plans(client)
    second = await _plans(client)
    assert {slug: plan["id"] for slug, plan in first.items()} == {
        slug: plan["id"] for slug, plan in second.items()
    }
    async with async_session_maker() as session:
        from sqlalchemy import func, select

        count = await session.scalar(select(func.count()).select_from(BillingPlan))
        assert count == len(first) == 4
        assert uuid.UUID(first["pro"]["id"])


async def test_metadata_survives_a_stripe_sdk_object(client, stripe_prices, fake_stripe, verified_signup):
    """Real webhooks arrive as `StripeObject`s, which are not dicts and expose no
    keys()/items()/get() — `dict(obj)` raises KeyError. Hand-built dicts in the other
    tests cannot catch that, so this one uses the SDK's own object."""
    from stripe._stripe_object import StripeObject

    user_id, token = await _login(client)
    plans = await _plans(client)

    subscription_object = StripeObject._construct_from(
        values={
            "id": "sub_test_sdk_object",
            "object": "subscription",
            "customer": "cus_test_123",
            "status": "active",
            "metadata": {"user_id": user_id, "plan_id": plans["pro"]["id"]},
            "items": {"data": [{"price": {"id": ANNUAL_IDS["pro"]}, "current_period_end": 1_800_000_000}]},
            "cancel_at_period_end": False,
        },
        requestor=None,
        api_mode="V1",
    )

    async with async_session_maker() as session:
        synced = await BillingService(session).sync_subscription_object(subscription_object)
        await session.commit()
        assert synced is not None
        assert synced.stripe_price_id == ANNUAL_IDS["pro"]

    body = (
        await client.get("/billing/subscription", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert body["status"] == "active"
    assert body["interval"] == "year"
    assert body["amount_cents"] == 49000


async def test_signed_webhook_activates_the_subscription_and_the_allowance(
    client, stripe_prices, verified_signup, monkeypatch
):
    """The whole webhook path: Stripe's own signature check, SDK object parsing,
    subscription sync, and the token allowance that inference actually spends."""
    import hashlib
    import hmac
    import json
    import time

    from pydantic import SecretStr

    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_secret"))

    user_id, token = await _login(client)
    plans = await _plans(client)

    event = {
        "id": "evt_test_signed_1",
        "object": "event",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": "sub_test_signed",
                "object": "subscription",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id, "plan_id": plans["business"]["id"]},
                "items": {"data": [{"price": {"id": MONTHLY_IDS["business"]}}]},
                "cancel_at_period_end": False,
            }
        },
    }
    payload = json.dumps(event).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"whsec_test_secret", f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()

    accepted = await client.post(
        "/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"received": True, "processed": True}

    # Replaying the same event must not double-apply it.
    replay = await client.post(
        "/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
    )
    assert replay.json() == {"received": True, "processed": False}

    tampered = await client.post(
        "/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={'0' * 64}", "Content-Type": "application/json"},
    )
    assert tampered.status_code == 400
    assert tampered.json()["detail"]["code"] == "invalid_stripe_signature"

    subscription = (
        await client.get("/billing/subscription", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert subscription["status"] == "active"
    assert subscription["plan_name"] == "Business"
    assert subscription["interval"] == "month"
    assert subscription["amount_cents"] == 29900

    # This event carries no period bounds (newer Stripe API versions put them on the
    # subscription item). A paying customer must still get the plan's allowance, not
    # the free tier's.
    allowance = (
        await client.get("/usage/allowance", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert allowance["stripe_monthly_allowance"] == 100_000_000
    assert allowance["total_monthly_allowance"] == 100_000_000


async def test_unreadable_webhook_body_is_not_reported_as_a_bad_signature(
    client, verified_signup, monkeypatch
):
    """A malformed body used to surface as 'invalid_stripe_signature', which sends
    operators hunting for the wrong misconfiguration."""
    import hashlib
    import hmac
    import time

    from pydantic import SecretStr

    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_secret"))

    payload = b"{not json"
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"whsec_test_secret", f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()

    response = await client.post(
        "/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_stripe_payload"


async def test_switching_plan_updates_the_subscription_in_place(
    client, stripe_prices, fake_stripe, verified_signup, monkeypatch
):
    """A subscriber cannot use checkout again, and the Stripe customer portal can only
    switch plans when the account has portal plan-switching configured. The change
    endpoint updates the subscription item directly."""
    user_id, token = await _login(client)
    plans = await _plans(client)

    async with async_session_maker() as session:
        await BillingService(session).sync_subscription_object(
            {
                "id": "sub_test_switch",
                "customer": "cus_test_123",
                "status": "active",
                "metadata": {"user_id": user_id, "plan_id": plans["starter"]["id"]},
                "items": {"data": [{"id": "si_test_1", "price": {"id": MONTHLY_IDS["starter"]}}]},
                "cancel_at_period_end": False,
            }
        )
        await session.commit()

    recorded: dict = {}

    def retrieve_subscription(self, subscription_id):
        return {
            "id": subscription_id,
            "customer": "cus_test_123",
            "status": "active",
            "items": {"data": [{"id": "si_test_1", "price": {"id": MONTHLY_IDS["starter"]}}]},
        }

    def update_subscription_price(self, *, subscription_id, subscription_item_id, price_id, user_id, plan_id):
        recorded.update(
            {
                "subscription_id": subscription_id,
                "subscription_item_id": subscription_item_id,
                "price_id": price_id,
                "user_id": user_id,
                "plan_id": plan_id,
            }
        )
        # Real Stripe does not rewrite a subscription's own metadata just because
        # its price changed — it stays whatever Checkout set it to. Returning the
        # *old* (Starter) metadata here, instead of conveniently pre-updated Pro
        # metadata, is what actually exercises the price -> plan fallback: without
        # it, this test would pass even if the sync kept the customer on the plan
        # they started on.
        return {
            "id": subscription_id,
            "customer": "cus_test_123",
            "status": "active",
            "metadata": {"user_id": user_id, "plan_id": plans["starter"]["id"]},
            "items": {"data": [{"id": subscription_item_id, "price": {"id": price_id}}]},
            "cancel_at_period_end": False,
        }

    monkeypatch.setattr(fake_stripe, "retrieve_subscription", retrieve_subscription, raising=False)
    monkeypatch.setattr(fake_stripe, "update_subscription_price", update_subscription_price, raising=False)

    response = await client.post(
        "/billing/subscription/change",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"], "interval": "year"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan_name"] == "Pro"
    assert body["interval"] == "year"
    assert body["amount_cents"] == 49000
    assert recorded == {
        "subscription_id": "sub_test_switch",
        "subscription_item_id": "si_test_1",
        "price_id": ANNUAL_IDS["pro"],
        "user_id": user_id,
        "plan_id": plans["pro"]["id"],
    }

    allowance = (
        await client.get("/usage/allowance", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert allowance["stripe_monthly_allowance"] == 10_000_000

    # The paid allowance follows the new plan immediately.
    allowance = (
        await client.get("/usage/allowance", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert allowance["stripe_monthly_allowance"] == 10_000_000

    # Asking for the plan it is already on is refused rather than churning Stripe.
    again = await client.post(
        "/billing/subscription/change",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"], "interval": "year"},
    )
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "already_on_plan"


async def test_switching_plan_without_a_subscription_is_refused(
    client, stripe_prices, fake_stripe, verified_signup
):
    _, token = await _login(client)
    plans = await _plans(client)

    response = await client.post(
        "/billing/subscription/change",
        headers={"Authorization": f"Bearer {token}"},
        json={"plan_id": plans["pro"]["id"]},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "no_active_subscription"
