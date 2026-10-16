"""HYP-94: a *scheduled* cancellation must not end the paid plan early.

Stripe populates `canceled_at` as soon as a cancellation is scheduled
(cancel_at_period_end=true), while the subscription itself stays "active" and
paid through current_period_end. The sync used to read any populated
`canceled_at` as "canceled", which dropped the customer to the free allowance
the moment they clicked Cancel. Stripe's own `status` is authoritative.
"""

import hashlib
import hmac
import json
import time

import pytest
from pydantic import SecretStr

from app.core.config import settings

BUSINESS_MONTHLY = "price_test_business_month"


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", True)
    monkeypatch.setattr(settings, "stripe_business_price_id", BUSINESS_MONTHLY)
    monkeypatch.setattr(settings, "stripe_secret_key", SecretStr("sk_test_dummy"))
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test_secret"))


async def _login(client) -> tuple[str, str]:
    register = await client.post(
        "/auth/register",
        json={"email": "hyp94@example.com", "password": "B1lling-Passw0rd!", "full_name": "Cancel User"},
    )
    assert register.status_code == 201, register.text
    login = await client.post(
        "/auth/login", json={"email": "hyp94@example.com", "password": "B1lling-Passw0rd!"}
    )
    assert login.status_code == 200, login.text
    return register.json()["id"], login.json()["access_token"]


async def _send_webhook(client, event: dict) -> dict:
    payload = json.dumps(event).encode()
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


def _subscription(user_id: str, **overrides) -> dict:
    now = int(time.time())
    return {
        "id": "sub_hyp94",
        "object": "subscription",
        "customer": "cus_hyp94",
        "status": "active",
        "metadata": {"user_id": user_id},
        "items": {"data": [{"price": {"id": BUSINESS_MONTHLY}}]},
        "current_period_start": now - 86400,
        "current_period_end": now + 29 * 86400,
        "cancel_at_period_end": False,
        "canceled_at": None,
        **overrides,
    }


async def test_scheduled_cancellation_keeps_the_plan_and_allowance_until_period_end(
    client, stripe_env
):
    user_id, token = await _login(client)
    auth = {"Authorization": f"Bearer {token}"}

    created = await _send_webhook(
        client,
        {
            "id": "evt_hyp94_created",
            "object": "event",
            "type": "customer.subscription.created",
            "data": {"object": _subscription(user_id)},
        },
    )
    assert created["processed"] is True

    # What Stripe actually sends when the customer cancels "at period end":
    # canceled_at is set, but status is still "active".
    scheduled = await _send_webhook(
        client,
        {
            "id": "evt_hyp94_scheduled",
            "object": "event",
            "type": "customer.subscription.updated",
            "data": {
                "object": _subscription(
                    user_id,
                    cancel_at_period_end=True,
                    canceled_at=int(time.time()),
                    cancel_at=int(time.time()) + 29 * 86400,
                )
            },
        },
    )
    assert scheduled["processed"] is True

    subscription = (await client.get("/billing/subscription", headers=auth)).json()
    assert subscription["status"] == "active"
    assert subscription["cancel_at_period_end"] is True
    assert subscription["plan_name"] == "Business"

    allowance = (await client.get("/usage/allowance", headers=auth)).json()
    assert allowance["stripe_monthly_allowance"] == 100_000_000

    # When the period really ends Stripe sends status="canceled" itself, and
    # only then does the paid allowance go away.
    ended = await _send_webhook(
        client,
        {
            "id": "evt_hyp94_deleted",
            "object": "event",
            "type": "customer.subscription.deleted",
            "data": {
                "object": _subscription(
                    user_id,
                    status="canceled",
                    cancel_at_period_end=True,
                    canceled_at=int(time.time()),
                )
            },
        },
    )
    assert ended["processed"] is True
    subscription = (await client.get("/billing/subscription", headers=auth)).json()
    assert subscription["status"] == "canceled"
    allowance = (await client.get("/usage/allowance", headers=auth)).json()
    assert allowance["stripe_monthly_allowance"] != 100_000_000
