"""HYP-66: pay-as-you-go wallet top-ups.

A user can buy a fixed token bundle through a one-time (mode="payment") Stripe
Checkout Session; completing it credits `UserWallet.balance_tokens`. Once the
subscription allowance for the current usage period is exhausted,
`UsageService.reserve_inference_tokens`/`settle_inference_usage` draw the rest
from that wallet instead of 402ing immediately -- but the wallet itself must
never go negative.

Mirrors the style of tests/test_billing_stripe.py (FakeStripeClient, signed
webhook helper) and tests/test_hyp36_model_pricing.py (UsageService exercised
directly against a seeded user, for the parts that are not HTTP-shaped).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.billing.plans import WALLET_TOPUP_BUNDLES
from app.core.config import settings
from app.core.security import utcnow
from app.db.models.manual_token_adjustment import ManualTokenAdjustment
from app.db.models.user import User
from app.db.models.user_wallet import UserWallet
from app.db.models.wallet_transaction import WalletTransaction
from app.db.session import async_session_maker
from app.usage.service import UsageService


class FakeStripeClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def create_customer(self, *, email: str, name: str | None, user_id: str) -> str:
        self.calls.append(("create_customer", {"email": email, "user_id": user_id}))
        return "cus_test_wallet"

    def create_wallet_checkout_session(self, *, customer_id, user_id, bundle_id, usd_cents, tokens, currency="usd"):
        self.calls.append(
            (
                "create_wallet_checkout_session",
                {
                    "customer_id": customer_id,
                    "user_id": user_id,
                    "bundle_id": bundle_id,
                    "usd_cents": usd_cents,
                    "tokens": tokens,
                },
            )
        )
        return {"id": "cs_test_wallet_1", "url": "https://checkout.stripe.com/c/pay/cs_test_wallet_1"}

    def create_checkout_session(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def create_customer_portal_session(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def cancel_subscription(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def retrieve_subscription(self, *args, **kwargs):
        raise AssertionError("not used in these tests")

    def retrieve_checkout_session(self, *args, **kwargs):
        raise AssertionError("not used in these tests")


@pytest.fixture
def fake_stripe(monkeypatch):
    FakeStripeClient.calls = []
    monkeypatch.setattr("app.billing.service.StripeClient", FakeStripeClient)
    return FakeStripeClient


@pytest.fixture
def verified_signup(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", True)


async def _login(client, email: str = "wallet@example.com") -> tuple[str, str]:
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": "W4llet-Passw0rd!", "full_name": "Wallet User"},
    )
    assert register.status_code == 201, register.text
    user_id = register.json()["id"]
    login = await client.post("/auth/login", json={"email": email, "password": "W4llet-Passw0rd!"})
    assert login.status_code == 200, login.text
    return user_id, login.json()["access_token"]


def _signed_webhook_headers(payload: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"whsec_test_wallet_secret", f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return {"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"}


async def _seed_user(session, *, manual_allowance: int = 0, wallet_balance: int | None = None) -> User:
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
    if manual_allowance:
        session.add(
            ManualTokenAdjustment(
                id=uuid.uuid4(),
                user_id=user.id,
                amount=manual_allowance,
                reason="hyp-66 test allowance",
                created_at=utcnow(),
            )
        )
    if wallet_balance is not None:
        session.add(UserWallet(user_id=user.id, balance_tokens=wallet_balance))
    await session.flush()
    return user


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def test_wallet_bundles_are_listed(client):
    response = await client.get("/billing/wallet/bundles")
    assert response.status_code == 200, response.text
    bundles = {bundle["id"]: bundle for bundle in response.json()}
    assert set(bundles) == {bundle.id for bundle in WALLET_TOPUP_BUNDLES}
    for bundle in WALLET_TOPUP_BUNDLES:
        assert bundles[bundle.id]["usd_cents"] == bundle.usd_cents
        assert bundles[bundle.id]["tokens"] == bundle.tokens


async def test_wallet_balance_starts_at_zero(client, verified_signup):
    _, token = await _login(client)
    response = await client.get("/billing/wallet", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json() == {"balance_tokens": 0}


async def test_wallet_checkout_session_creates_a_one_time_payment_session(
    client, fake_stripe, verified_signup
):
    _, token = await _login(client)

    response = await client.post(
        "/billing/wallet/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"bundle_id": "topup_25"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://checkout.stripe.com/")

    call = dict(fake_stripe.calls)["create_wallet_checkout_session"]
    bundle = next(b for b in WALLET_TOPUP_BUNDLES if b.id == "topup_25")
    assert call["bundle_id"] == "topup_25"
    assert call["usd_cents"] == bundle.usd_cents
    assert call["tokens"] == bundle.tokens


async def test_wallet_checkout_session_rejects_an_unknown_bundle(client, fake_stripe, verified_signup):
    _, token = await _login(client)

    response = await client.post(
        "/billing/wallet/checkout-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"bundle_id": "not_a_real_bundle"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "unknown_wallet_bundle"


async def test_webhook_credits_the_wallet_exactly_once(client, verified_signup, monkeypatch):
    """The whole webhook path for a wallet top-up: signature check, purpose
    routing away from the subscription sync path, crediting the balance, and
    StripeWebhookEvent's dedupe so a retried delivery never double-credits."""
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_wallet_secret"))

    user_id, token = await _login(client)
    bundle = next(b for b in WALLET_TOPUP_BUNDLES if b.id == "topup_10")

    event = {
        "id": "evt_test_wallet_topup_1",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_wallet_topup_1",
                "object": "checkout.session",
                "customer": "cus_test_wallet",
                "client_reference_id": user_id,
                "payment_intent": "pi_test_wallet_1",
                "metadata": {
                    "purpose": "wallet_topup",
                    "user_id": user_id,
                    "bundle_id": bundle.id,
                    "tokens": str(bundle.tokens),
                },
            }
        },
    }
    payload = json.dumps(event).encode()

    first = await client.post(
        "/billing/stripe/webhook", content=payload, headers=_signed_webhook_headers(payload)
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"received": True, "processed": True}

    balance = await client.get("/billing/wallet", headers={"Authorization": f"Bearer {token}"})
    assert balance.json()["balance_tokens"] == bundle.tokens

    # Stripe redelivering the same event must not double-credit.
    replay = await client.post(
        "/billing/stripe/webhook", content=payload, headers=_signed_webhook_headers(payload)
    )
    assert replay.json() == {"received": True, "processed": False}

    balance_after_replay = await client.get(
        "/billing/wallet", headers={"Authorization": f"Bearer {token}"}
    )
    assert balance_after_replay.json()["balance_tokens"] == bundle.tokens

    async with async_session_maker() as session:
        transactions = (
            await session.execute(
                select(WalletTransaction).where(WalletTransaction.user_id == uuid.UUID(user_id))
            )
        ).scalars().all()
        assert len(transactions) == 1
        assert transactions[0].kind == "purchase"
        assert transactions[0].delta_tokens == bundle.tokens
        assert transactions[0].stripe_payment_intent_id == "pi_test_wallet_1"


async def test_spend_draws_from_allowance_before_the_wallet():
    """Usage under the subscription allowance must never touch the wallet."""
    async with async_session_maker() as session:
        user = await _seed_user(session, manual_allowance=10_000, wallet_balance=5_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=4_000, max_completion_tokens=0
        )
        assert event.reserved_tokens == 4_000
        assert event.wallet_reserved_tokens == 0

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 5_000

        period = await service.current_period(user)
        assert period.remaining_tokens == 6_000


async def test_spend_overflows_into_the_wallet_once_allowance_is_exhausted():
    async with async_session_maker() as session:
        user = await _seed_user(session, manual_allowance=1_000, wallet_balance=5_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=3_500, max_completion_tokens=0
        )
        # 1,000 from the allowance, the remaining 2,500 from the wallet.
        assert event.reserved_tokens == 3_500
        assert event.wallet_reserved_tokens == 2_500

        period = await service.current_period(user)
        assert period.used_tokens == 1_000
        assert period.remaining_tokens == 0

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 5_000 - 2_500

        settled = await service.settle_inference_usage(event, prompt_tokens=3_500, completion_tokens=0)
        assert settled.charged_tokens == 3_500
        assert settled.wallet_reserved_tokens == 2_500

        transactions = (
            await session.execute(
                select(WalletTransaction).where(WalletTransaction.user_id == user.id)
            )
        ).scalars().all()
        assert len(transactions) == 1
        assert transactions[0].kind == "debit"
        assert transactions[0].delta_tokens == -2_500


async def test_settlement_refunds_the_unused_wallet_portion_of_a_reservation():
    """The reservation estimate drew from the wallet, but actual usage came in
    lower than estimated -- the excess must be refunded to the wallet, not just
    dropped, mirroring how the allowance side already un-reserves its excess."""
    async with async_session_maker() as session:
        user = await _seed_user(session, manual_allowance=2_000, wallet_balance=5_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="small-model",
            prompt_tokens=1_000,
            max_completion_tokens=3_000,  # estimate: 4,000 raw -> 2,000 allowance + 2,000 wallet
        )
        assert event.wallet_reserved_tokens == 2_000

        settled = await service.settle_inference_usage(
            event, prompt_tokens=1_000, completion_tokens=200  # actual: 1,200 raw, all within allowance
        )
        assert settled.charged_tokens == 1_200
        assert settled.wallet_reserved_tokens == 0

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 5_000  # fully refunded

        period = await service.current_period(user)
        assert period.used_tokens == 1_200
        assert period.remaining_tokens == 800

        kinds = sorted(
            transaction.kind
            for transaction in (
                await session.execute(
                    select(WalletTransaction).where(WalletTransaction.user_id == user.id)
                )
            ).scalars()
        )
        assert kinds == ["debit", "refund"]


async def test_wallet_never_goes_negative_and_still_402s_when_exhausted():
    async with async_session_maker() as session:
        user = await _seed_user(session, manual_allowance=1_000, wallet_balance=500)
        service = UsageService(session)

        from app.core.errors import payment_required  # noqa: F401  (documents the raised error)

        with pytest.raises(Exception) as excinfo:
            await service.reserve_inference_tokens(
                user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=2_000, max_completion_tokens=0
            )
        assert getattr(excinfo.value, "status_code", None) == 402

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 500  # untouched -- nothing partially reserved

        period = await service.current_period(user)
        assert period.used_tokens == 0
        assert period.remaining_tokens == 1_000


async def test_a_reservation_released_before_settling_refunds_the_wallet():
    async with async_session_maker() as session:
        user = await _seed_user(session, manual_allowance=1_000, wallet_balance=5_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=3_500, max_completion_tokens=0
        )
        assert event.wallet_reserved_tokens == 2_500

        await service.release_inference_reservation(event)

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 5_000

        period = await service.current_period(user)
        assert period.used_tokens == 0
        assert period.remaining_tokens == 1_000
