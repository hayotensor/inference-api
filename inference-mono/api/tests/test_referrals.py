"""HYP-65: referral system.

A user gets a unique shareable referral code (lazily, on first GET
/referrals/me); an optional `referral_code` on signup attributes the new
account to whoever's code it was, permanently. When the referred user later
pays Stripe (subscription invoice.paid, or a HYP-66 wallet top-up), the
REFERRER's wallet is credited 10% of that payment in bonus tokens, converted
at the payment's own effective $/token rate -- never as a cash payout.

Mirrors the FakeStripeClient/signed-webhook-helper style of
tests/test_billing_stripe.py and tests/test_wallet_topup.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from app.core.config import settings
from app.core.errors import bad_request
from app.core.security import utcnow
from app.db.models.referral import Referral
from app.db.models.referral_earning import ReferralEarning
from app.db.models.user import User
from app.db.models.wallet_transaction import WalletTransaction
from app.db.session import async_session_maker
from app.referrals.service import ReferralService

STARTER_PRICE_ID = "price_test_referral_starter_month"
BUSINESS_PRICE_ID = "price_test_referral_business_month"


class FakeStripeClient:
    """Records calls; `subscriptions` lets a test register the object
    `retrieve_subscription` should hand back for a given subscription id
    (used by the invoice.paid -> retrieve_subscription -> sync path)."""

    calls: list[tuple[str, dict]] = []
    subscriptions: dict[str, dict] = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    def create_customer(self, *, email: str, name: str | None, user_id: str) -> str:
        self.calls.append(("create_customer", {"email": email, "user_id": user_id}))
        return f"cus_test_{user_id[:8]}"

    def retrieve_subscription(self, subscription_id: str):
        return FakeStripeClient.subscriptions[subscription_id]

    def construct_webhook_event(self, *, payload: bytes, signature: str) -> dict:
        """A faithful-enough stand-in for the real StripeClient's signature
        check (needed here because -- unlike the other billing webhook tests,
        which never exercise invoice.paid's retrieve_subscription call --
        this suite needs `app.billing.service.StripeClient` swapped out
        entirely so FakeStripeClient.retrieve_subscription answers it)."""
        try:
            parts = dict(part.split("=", 1) for part in signature.split(","))
            timestamp, expected_sig = parts["t"], parts["v1"]
        except Exception as exc:  # noqa: BLE001
            raise bad_request("invalid_stripe_payload", "Unreadable Stripe webhook payload") from exc
        secret = settings.stripe_webhook_secret.get_secret_value().encode()
        computed = hmac.new(secret, f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, expected_sig):
            raise bad_request("invalid_stripe_signature", "Invalid Stripe webhook signature")
        return json.loads(payload)

    def create_wallet_checkout_session(self, *, customer_id, user_id, bundle_id, usd_cents, tokens, currency="usd"):
        self.calls.append(
            ("create_wallet_checkout_session", {"user_id": user_id, "bundle_id": bundle_id})
        )
        return {"id": f"cs_test_{bundle_id}", "url": "https://checkout.stripe.com/c/pay/cs_test_topup"}

    def create_checkout_session(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def create_customer_portal_session(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def cancel_subscription(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def update_subscription_price(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def retrieve_checkout_session(self, *args, **kwargs):
        raise AssertionError("not used in these tests")


@pytest.fixture
def fake_stripe(monkeypatch):
    FakeStripeClient.calls = []
    FakeStripeClient.subscriptions = {}
    monkeypatch.setattr("app.billing.service.StripeClient", FakeStripeClient)
    return FakeStripeClient


@pytest.fixture
def stripe_prices(monkeypatch):
    monkeypatch.setattr(settings, "stripe_starter_price_id", STARTER_PRICE_ID)
    monkeypatch.setattr(settings, "stripe_business_price_id", BUSINESS_PRICE_ID)


@pytest.fixture
def verified_signup(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", True)


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def _register(client, email: str, *, referral_code: str | None = None) -> tuple[str, str]:
    payload = {"email": email, "password": "Ref3rral-Passw0rd!", "full_name": "Referral User"}
    if referral_code is not None:
        payload["referral_code"] = referral_code
    register = await client.post("/auth/register", json=payload)
    assert register.status_code == 201, register.text
    user_id = register.json()["id"]
    login = await client.post("/auth/login", json={"email": email, "password": "Ref3rral-Passw0rd!"})
    assert login.status_code == 200, login.text
    return user_id, login.json()["access_token"]


async def _my_referral_summary(client, token: str) -> dict:
    response = await client.get("/referrals/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


async def _plans(client) -> dict[str, dict]:
    response = await client.get("/billing/plans")
    assert response.status_code == 200, response.text
    return {plan["slug"]: plan for plan in response.json()}


def _signed_webhook_headers(payload: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"whsec_test_referral_secret", f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"}


def _subscription_object(sub_id: str, *, customer_id: str, user_id: str, plan_id: str, price_id: str) -> dict:
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": customer_id,
        "status": "active",
        "metadata": {"user_id": user_id, "plan_id": plan_id},
        "items": {"data": [{"price": {"id": price_id}}]},
        "cancel_at_period_end": False,
    }


def _invoice_object(invoice_id: str, *, subscription_id: str, customer_id: str, amount_paid: int) -> dict:
    return {
        "id": invoice_id,
        "object": "invoice",
        "customer": customer_id,
        "subscription": subscription_id,
        "amount_paid": amount_paid,
        "status": "paid",
    }


async def _post_webhook(client, event_id: str, event_type: str, stripe_object: dict):
    event = {"id": event_id, "object": "event", "type": event_type, "data": {"object": stripe_object}}
    payload = json.dumps(event).encode()
    return await client.post(
        "/billing/stripe/webhook", content=payload, headers=_signed_webhook_headers(payload)
    )


async def _activate_subscription(
    client, fake_stripe, *, user_id: str, plan_id: str, price_id: str, customer_id: str, sub_id: str
) -> None:
    subscription = _subscription_object(
        sub_id, customer_id=customer_id, user_id=user_id, plan_id=plan_id, price_id=price_id
    )
    fake_stripe.subscriptions[sub_id] = subscription
    response = await _post_webhook(client, f"evt_sub_created_{sub_id}", "customer.subscription.created", subscription)
    assert response.json() == {"received": True, "processed": True}


async def test_referral_code_is_generated_lazily_and_stable(client, verified_signup):
    _, token = await _register(client, "referrer-code@example.com")
    first = await _my_referral_summary(client, token)
    second = await _my_referral_summary(client, token)
    assert first["referral_code"] == second["referral_code"]
    assert len(first["referral_code"]) == 8
    assert first["referral_link"] == f"{settings.app_url}/signup?ref={first['referral_code']}"
    assert first["total_referrals"] == 0
    assert first["total_tokens_earned"] == 0
    assert first["recent_earnings"] == []


async def test_signup_with_a_valid_referral_code_attributes_correctly(client, verified_signup):
    referrer_id, referrer_token = await _register(client, "referrer1@example.com")
    code = (await _my_referral_summary(client, referrer_token))["referral_code"]

    referred_id, _ = await _register(client, "referred1@example.com", referral_code=code)

    async with async_session_maker() as session:
        referral = (
            await session.execute(
                select(Referral).where(Referral.referred_user_id == uuid.UUID(referred_id))
            )
        ).scalar_one()
        assert str(referral.referrer_user_id) == referrer_id
        assert referral.referral_code_used == code

    summary = await _my_referral_summary(client, referrer_token)
    assert summary["total_referrals"] == 1


async def test_signup_with_an_unknown_referral_code_is_ignored_not_rejected(client, verified_signup):
    referred_id, _ = await _register(
        client, "referred-bad-code@example.com", referral_code="NOTAREALCODE"
    )
    async with async_session_maker() as session:
        referral = (
            await session.execute(
                select(Referral).where(Referral.referred_user_id == uuid.UUID(referred_id))
            )
        ).scalar_one_or_none()
        assert referral is None


async def test_attribute_signup_refuses_a_literal_self_referral(client, verified_signup):
    """Not reachable through the public API today (the referred account doesn't
    exist yet when its own future code could be read) -- exercised directly at
    the service layer as a belt-and-suspenders regression guard."""
    async with async_session_maker() as session:
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
        service = ReferralService(session)
        code = await service.ensure_referral_code(user)
        await service.attribute_signup(user, code)
        await session.commit()

        referral = (
            await session.execute(select(Referral).where(Referral.referred_user_id == user.id))
        ).scalar_one_or_none()
        assert referral is None


async def test_paid_invoice_credits_the_referrer_wallet_exactly_once(
    client, fake_stripe, stripe_prices, verified_signup, monkeypatch
):
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_referral_secret"))

    referrer_id, referrer_token = await _register(client, "referrer2@example.com")
    code = (await _my_referral_summary(client, referrer_token))["referral_code"]
    referred_id, _ = await _register(client, "referred2@example.com", referral_code=code)

    plans = await _plans(client)
    starter = plans["starter"]
    assert starter["price_cents"] == 900
    assert starter["monthly_token_allowance"] == 1_000_000

    await _activate_subscription(
        client,
        fake_stripe,
        user_id=referred_id,
        plan_id=starter["id"],
        price_id=STARTER_PRICE_ID,
        customer_id="cus_test_referred2",
        sub_id="sub_test_referred2",
    )

    invoice = _invoice_object(
        "in_test_referred2_1", subscription_id="sub_test_referred2", customer_id="cus_test_referred2",
        amount_paid=900,
    )
    first = await _post_webhook(client, "evt_invoice_paid_1", "invoice.paid", invoice)
    assert first.json() == {"received": True, "processed": True}

    # 10% of $9.00 = $0.90, at Starter's own $9/1,000,000-token rate -> 100,000 tokens.
    balance = await client.get(
        "/billing/wallet", headers={"Authorization": f"Bearer {referrer_token}"}
    )
    assert balance.json()["balance_tokens"] == 100_000

    async with async_session_maker() as session:
        earnings = (
            await session.execute(
                select(ReferralEarning).where(ReferralEarning.referrer_user_id == uuid.UUID(referrer_id))
            )
        ).scalars().all()
        assert len(earnings) == 1
        assert earnings[0].tokens_credited == 100_000
        assert earnings[0].usd_equivalent_cents == 90
        assert earnings[0].source_id == "in_test_referred2_1"

        transactions = (
            await session.execute(
                select(WalletTransaction).where(WalletTransaction.user_id == uuid.UUID(referrer_id))
            )
        ).scalars().all()
        assert len(transactions) == 1
        assert transactions[0].kind == "referral_bonus"
        assert transactions[0].delta_tokens == 100_000

    # A retried webhook delivery (same Stripe event id) must not double-credit.
    replay = await _post_webhook(client, "evt_invoice_paid_1", "invoice.paid", invoice)
    assert replay.json() == {"received": True, "processed": False}
    balance_after_replay = await client.get(
        "/billing/wallet", headers={"Authorization": f"Bearer {referrer_token}"}
    )
    assert balance_after_replay.json()["balance_tokens"] == 100_000

    summary = await _my_referral_summary(client, referrer_token)
    assert summary["total_referrals"] == 1
    assert summary["total_tokens_earned"] == 100_000
    assert len(summary["recent_earnings"]) == 1
    assert summary["recent_earnings"][0]["tokens_credited"] == 100_000


async def test_no_referral_credit_past_the_twelve_month_earnout_window(
    client, fake_stripe, stripe_prices, verified_signup, monkeypatch
):
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_referral_secret"))

    referrer_id, referrer_token = await _register(client, "referrer3@example.com")
    code = (await _my_referral_summary(client, referrer_token))["referral_code"]
    referred_id, _ = await _register(client, "referred3@example.com", referral_code=code)

    async with async_session_maker() as session:
        await session.execute(
            update(Referral)
            .where(Referral.referred_user_id == uuid.UUID(referred_id))
            .values(created_at=utcnow() - timedelta(days=400))
        )
        await session.commit()

    plans = await _plans(client)
    starter = plans["starter"]
    await _activate_subscription(
        client,
        fake_stripe,
        user_id=referred_id,
        plan_id=starter["id"],
        price_id=STARTER_PRICE_ID,
        customer_id="cus_test_referred3",
        sub_id="sub_test_referred3",
    )
    invoice = _invoice_object(
        "in_test_referred3_1", subscription_id="sub_test_referred3", customer_id="cus_test_referred3",
        amount_paid=900,
    )
    response = await _post_webhook(client, "evt_invoice_paid_expired", "invoice.paid", invoice)
    assert response.json() == {"received": True, "processed": True}

    balance = await client.get(
        "/billing/wallet", headers={"Authorization": f"Bearer {referrer_token}"}
    )
    assert balance.json()["balance_tokens"] == 0

    async with async_session_maker() as session:
        earnings = (
            await session.execute(
                select(ReferralEarning).where(ReferralEarning.referrer_user_id == uuid.UUID(referrer_id))
            )
        ).scalars().all()
        assert earnings == []


async def test_wallet_topup_by_a_referred_user_credits_the_referrer(
    client, fake_stripe, verified_signup, monkeypatch
):
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_referral_secret"))

    referrer_id, referrer_token = await _register(client, "referrer4@example.com")
    code = (await _my_referral_summary(client, referrer_token))["referral_code"]
    referred_id, _ = await _register(client, "referred4@example.com", referral_code=code)

    checkout_session = {
        "id": "cs_test_referred4_topup",
        "object": "checkout.session",
        "customer": "cus_test_referred4",
        "client_reference_id": referred_id,
        "payment_intent": "pi_test_referred4_topup",
        "metadata": {
            "purpose": "wallet_topup",
            "user_id": referred_id,
            "bundle_id": "topup_10",
            "tokens": "1000000",
        },
    }
    response = await _post_webhook(
        client, "evt_topup_referred4", "checkout.session.completed", checkout_session
    )
    assert response.json() == {"received": True, "processed": True}

    # topup_10 = $10.00 for 1,000,000 tokens ($0.001/token); 10% of $10 = $1.00 ->
    # 1,000 * ($0.01 / $0.001-per-token)... i.e. 100 cents / 0.001 cents-per-token.
    balance = await client.get(
        "/billing/wallet", headers={"Authorization": f"Bearer {referrer_token}"}
    )
    assert balance.json()["balance_tokens"] == 100_000

    async with async_session_maker() as session:
        earnings = (
            await session.execute(
                select(ReferralEarning).where(ReferralEarning.referrer_user_id == uuid.UUID(referrer_id))
            )
        ).scalars().all()
        assert len(earnings) == 1
        assert earnings[0].tokens_credited == 100_000
        assert earnings[0].source_id == "pi_test_referred4_topup"
