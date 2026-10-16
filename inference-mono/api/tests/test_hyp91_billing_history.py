"""HYP-91: GET /billing/history -- the user's own billing events, newest first.

Drives the real Stripe webhook path (signed events) through a subscription's
whole life -- created, plan change, cancellation scheduled and withdrawn,
renewal invoice, wallet top-up, cancellation -- and checks what the history
endpoint reports, its pagination, idempotency against re-delivered webhooks,
and that one user can never see another's history.
"""

import hashlib
import hmac
import json
import time

import pytest
from pydantic import SecretStr

from app.billing.stripe import StripeClient
from app.core.config import settings

PRICES = {"pro": "price_test_pro_month", "business": "price_test_business_month"}


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", True)
    for slug, price_id in PRICES.items():
        monkeypatch.setattr(settings, f"stripe_{slug}_price_id", price_id)
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_secret"))


async def _login(client, email: str) -> tuple[str, dict[str, str]]:
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": "B1lling-Passw0rd!", "full_name": "History User"},
    )
    assert register.status_code == 201, register.text
    login = await client.post("/auth/login", json={"email": email, "password": "B1lling-Passw0rd!"})
    assert login.status_code == 200, login.text
    return register.json()["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _send(client, event_id: str, event_type: str, obj: dict) -> dict:
    payload = json.dumps(
        {"id": event_id, "object": "event", "type": event_type, "data": {"object": obj}}
    ).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"whsec_test_secret", f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    response = await client.post(
        "/billing/stripe/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature}", "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _sub(user_id: str, price: str, **overrides) -> dict:
    now = int(time.time())
    return {
        "id": "sub_hyp91",
        "object": "subscription",
        "customer": "cus_hyp91",
        "status": "active",
        "metadata": {"user_id": user_id},
        "items": {"data": [{"price": {"id": price}}]},
        "current_period_start": now,
        "current_period_end": now + 30 * 86400,
        "cancel_at_period_end": False,
        **overrides,
    }


async def _history(client, headers, **params) -> dict:
    response = await client.get("/billing/history", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_billing_history_records_the_subscription_lifecycle_newest_first(
    client, stripe_env, monkeypatch
):
    user_id, auth = await _login(client, "history@example.com")
    other_id, other_auth = await _login(client, "other@example.com")

    assert await _history(client, auth) == {"data": [], "total": 0, "limit": 20, "offset": 0}

    await _send(client, "evt_1", "customer.subscription.created", _sub(user_id, PRICES["business"]))
    # A re-sync of the same state (e.g. the /billing/success sync racing the
    # webhook) must not add a second "created" row.
    await _send(client, "evt_1b", "customer.subscription.updated", _sub(user_id, PRICES["business"]))
    await _send(client, "evt_2", "customer.subscription.updated", _sub(user_id, PRICES["pro"]))
    await _send(
        client,
        "evt_3",
        "customer.subscription.updated",
        _sub(user_id, PRICES["pro"], cancel_at_period_end=True, canceled_at=int(time.time())),
    )
    await _send(client, "evt_4", "customer.subscription.updated", _sub(user_id, PRICES["pro"]))

    monkeypatch.setattr(
        StripeClient, "retrieve_subscription", lambda self, sid: _sub(user_id, PRICES["pro"])
    )
    invoice = {
        "id": "in_hyp91_cycle",
        "object": "invoice",
        "subscription": "sub_hyp91",
        "customer": "cus_hyp91",
        "billing_reason": "subscription_cycle",
        "amount_paid": 4900,
        "currency": "usd",
    }
    await _send(client, "evt_5", "invoice.paid", invoice)
    # Stripe may deliver the same invoice under a new event id; still one row.
    await _send(client, "evt_5b", "invoice.paid", invoice)

    await _send(
        client,
        "evt_6",
        "checkout.session.completed",
        {
            "id": "cs_hyp91_topup",
            "object": "checkout.session",
            "mode": "payment",
            "customer": "cus_hyp91",
            "client_reference_id": user_id,
            "payment_intent": "pi_hyp91",
            "metadata": {
                "purpose": "wallet_topup",
                "user_id": user_id,
                "tokens": "5000000",
                "usd_cents": "2000",
            },
        },
    )
    await _send(
        client, "evt_7", "customer.subscription.deleted", _sub(user_id, PRICES["pro"], status="canceled")
    )

    history = await _history(client, auth)
    assert history["total"] == 7
    types = [row["event_type"] for row in history["data"]]
    assert types == [
        "subscription_canceled",
        "wallet_topup",
        "subscription_renewed",
        "subscription_reactivated",
        "subscription_cancel_scheduled",
        "subscription_updated",
        "subscription_created",
    ]
    by_type = {row["event_type"]: row for row in history["data"]}
    assert by_type["subscription_created"]["plan_name"] == "Business"
    assert by_type["subscription_created"]["amount_cents"] == 29900
    assert by_type["subscription_updated"]["description"] == (
        "Changed plan from Business (monthly) to Pro (monthly)"
    )
    assert by_type["subscription_renewed"]["amount_cents"] == 4900
    assert by_type["subscription_renewed"]["currency"] == "usd"
    assert by_type["wallet_topup"]["tokens"] == 5_000_000
    assert by_type["wallet_topup"]["amount_cents"] == 2000
    # Internal Stripe ids are not part of the user-facing payload.
    assert all("stripe_reference" not in row for row in history["data"])

    page = await _history(client, auth, limit=3, offset=3)
    assert page["total"] == 7 and page["limit"] == 3 and page["offset"] == 3
    assert [row["event_type"] for row in page["data"]] == types[3:6]

    # Strictly user-scoped.
    assert (await _history(client, other_auth))["total"] == 0
    assert other_id != user_id


async def test_billing_history_requires_auth_and_validates_paging(client, stripe_env):
    assert (await client.get("/billing/history")).status_code == 401
    _, auth = await _login(client, "paging@example.com")
    assert (await client.get("/billing/history", headers=auth, params={"limit": 0})).status_code == 422
    assert (await client.get("/billing/history", headers=auth, params={"limit": 101})).status_code == 422
    assert (await client.get("/billing/history", headers=auth, params={"offset": -1})).status_code == 422
