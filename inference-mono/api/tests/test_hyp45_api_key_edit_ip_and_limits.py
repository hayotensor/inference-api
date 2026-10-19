"""HYP-45 follow-ups (control plane): edit a key after creation, optional client
IP allowlist, and credit/USD spend caps with a daily/monthly/never period.

Enforcement lives on the serving plane (see
``inference_api/tests/test_hyp45_api_key_ip_and_spend_limits.py``); these cover
the management surface: create/PATCH validation + persistence and the
``GET /api-keys/{id}/spend`` readout.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.core.security import utcnow
from app.db.models.inference_usage_event import InferenceUsageEvent
from app.db.models.usage_period import UsagePeriod
from app.db.session import async_session_maker

from tests.test_api_keys import create_phone_verified_session


async def _auth(client, monkeypatch) -> dict[str, str]:
    session = await create_phone_verified_session(client, monkeypatch)
    return {"Authorization": f"Bearer {session['access_token']}"}


async def _create(client, headers, **extra):
    response = await client.post(
        "/api-keys", headers=headers, json={"name": "key", "environment": "test", **extra}
    )
    return response


async def test_defaults_are_unrestricted(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    created = await _create(client, headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["allowed_ips"] is None
    assert body["limit_period"] is None
    assert body["credit_limit"] is None
    assert body["usd_limit_cents"] is None


async def test_create_with_ip_allowlist_normalizes_entries(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    created = await _create(
        client,
        headers,
        allowed_ips=[" 203.0.113.5 ", "10.1.2.3/8", "2001:DB8:0:0::1", "203.0.113.5"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["allowed_ips"] == ["203.0.113.5", "10.0.0.0/8", "2001:db8::1"]


async def test_create_rejects_invalid_ip(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    created = await _create(client, headers, allowed_ips=["not-an-ip"])
    assert created.status_code == 422


async def test_create_with_limits(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    created = await _create(
        client, headers, limit_period="monthly", credit_limit=50_000, usd_limit_cents=2_500
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["limit_period"], body["credit_limit"], body["usd_limit_cents"]) == (
        "monthly",
        50_000,
        2_500,
    )


async def test_limits_require_a_period(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    assert (await _create(client, headers, credit_limit=100)).status_code == 422
    assert (await _create(client, headers, limit_period="weekly", credit_limit=100)).status_code == 422
    assert (await _create(client, headers, limit_period="daily", credit_limit=0)).status_code == 422


async def test_edit_name_models_ips_and_limits(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    key_id = (await _create(client, headers)).json()["id"]

    edited = await client.patch(
        f"/api-keys/{key_id}",
        headers=headers,
        json={
            "name": "renamed",
            "allowed_models": ["small-model"],
            "allowed_ips": ["198.51.100.0/24"],
            "limit_period": "daily",
            "credit_limit": 1_000,
            "usd_limit_cents": 500,
            "rate_limit_per_minute": 30,
        },
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["name"] == "renamed"
    assert body["allowed_models"] == ["small-model"]
    assert body["allowed_ips"] == ["198.51.100.0/24"]
    assert (body["limit_period"], body["credit_limit"], body["usd_limit_cents"]) == ("daily", 1_000, 500)
    assert body["rate_limit_per_minute"] == 30

    fetched = (await client.get(f"/api-keys/{key_id}", headers=headers)).json()
    assert fetched["name"] == "renamed"
    assert fetched["allowed_ips"] == ["198.51.100.0/24"]

    # Omitted fields stay put; explicit null clears an optional restriction.
    cleared = await client.patch(
        f"/api-keys/{key_id}",
        headers=headers,
        json={"allowed_ips": None, "allowed_models": [], "usd_limit_cents": None},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["allowed_ips"] is None
    assert body["allowed_models"] is None
    assert body["usd_limit_cents"] is None
    assert body["credit_limit"] == 1_000
    assert body["name"] == "renamed"


async def test_edit_cannot_drop_period_while_a_limit_remains(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    key_id = (await _create(client, headers, limit_period="daily", credit_limit=10)).json()["id"]
    response = await client.patch(f"/api-keys/{key_id}", headers=headers, json={"limit_period": None})
    assert response.status_code == 400
    # Clearing both together is fine.
    response = await client.patch(
        f"/api-keys/{key_id}", headers=headers, json={"limit_period": None, "credit_limit": None}
    )
    assert response.status_code == 200, response.text


async def test_edit_ignores_null_for_required_fields(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    key_id = (await _create(client, headers)).json()["id"]
    response = await client.patch(
        f"/api-keys/{key_id}", headers=headers, json={"name": None, "scopes": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "key"
    assert response.json()["scopes"]


async def test_revoked_key_cannot_be_edited(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    key_id = (await _create(client, headers)).json()["id"]
    assert (await client.delete(f"/api-keys/{key_id}", headers=headers)).status_code == 204
    response = await client.patch(f"/api-keys/{key_id}", headers=headers, json={"name": "x"})
    assert response.status_code == 400


async def test_cannot_edit_another_users_key(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    key_id = (await _create(client, headers)).json()["id"]
    response = await client.patch(
        f"/api-keys/{key_id}", headers={"Authorization": "Bearer nope"}, json={"name": "x"}
    )
    assert response.status_code == 401
    response = await client.patch(f"/api-keys/{uuid.uuid4()}", headers=headers, json={"name": "x"})
    assert response.status_code == 404


async def test_spend_readout_sums_the_current_period(client, monkeypatch):
    headers = await _auth(client, monkeypatch)
    created = (
        await _create(client, headers, limit_period="daily", credit_limit=10_000, usd_limit_cents=100)
    ).json()
    key_id = uuid.UUID(created["id"])
    user_id = uuid.UUID(created["user_id"])
    now = utcnow()
    async with async_session_maker() as session:
        period = UsagePeriod(user_id=user_id, period_start=now - timedelta(days=30), period_end=now + timedelta(days=1))
        session.add(period)
        await session.flush()

        def event(status: str, *, charged=0, reserved=0, wallet=0, created_at=now, expires_at=None):
            return InferenceUsageEvent(
                usage_period_id=period.id,
                user_id=user_id,
                api_key_id=key_id,
                request_id=uuid.uuid4().hex,
                model="small-model",
                charged_tokens=charged,
                reserved_tokens=reserved,
                wallet_reserved_tokens=wallet,
                status=status,
                expires_at=expires_at or now + timedelta(minutes=10),
                created_at=created_at,
            )

        session.add_all(
            [
                event("settled", charged=3_000, wallet=50_000),
                event("reserved", reserved=1_000),
                event("released", reserved=99_999),
                event("settled", charged=7_777, created_at=now - timedelta(days=2)),
            ]
        )
        await session.commit()

    spend = await client.get(f"/api-keys/{key_id}/spend", headers=headers)
    assert spend.status_code == 200, spend.text
    body = spend.json()
    assert body["credits_used"] == 4_000
    assert body["usd_cents_used"] == 50  # 50k wallet credits at $10/1M
    assert body["limit_period"] == "daily"
    assert body["resets_at"] is not None
