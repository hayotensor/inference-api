"""Hourly credit buckets for the dashboard's credits-over-time chart (HYP-90)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.core.security import as_utc, expires_in, generate_uuid
from app.db.models.inference_usage_event import InferenceUsageEvent
from app.db.models.user import User
from app.db.session import async_session_maker
from app.usage.service import UsageService

from tests.test_usage_by_model import _user_by_email, register_and_login


async def _seed_event(user_id, *, at_offset: timedelta, charged: int, status: str = "settled") -> datetime:
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        period = await UsageService(session).recalculate_current_period(user)
        # Offsets count from the hour after period start so buckets are predictable.
        start_hour = as_utc(period.period_start).replace(minute=0, second=0, microsecond=0)
        created_at = start_hour + timedelta(hours=1) + at_offset
        session.add(
            InferenceUsageEvent(
                usage_period_id=period.id,
                user_id=user_id,
                api_key_id=None,
                request_id=generate_uuid().hex,
                model="model-a",
                prompt_tokens=charged,
                completion_tokens=0,
                raw_total_tokens=charged,
                model_multiplier=Decimal("1"),
                reserved_tokens=charged,
                charged_tokens=charged,
                status=status,
                expires_at=expires_in(seconds=3600),
                settled_at=created_at,
                created_at=created_at,
            )
        )
        await session.commit()
        return created_at


async def test_usage_hourly_sums_counted_credits_per_hour(client):
    tokens = await register_and_login(client, "hourly@example.com")
    other = await register_and_login(client, "hourly-other@example.com")
    user = await _user_by_email("hourly@example.com")
    other_user = await _user_by_email("hourly-other@example.com")

    first = await _seed_event(user.id, at_offset=timedelta(hours=1, minutes=5), charged=100)
    await _seed_event(user.id, at_offset=timedelta(hours=1, minutes=50), charged=20)
    third = await _seed_event(user.id, at_offset=timedelta(hours=3, minutes=1), charged=7)
    # Released events cost nothing and another user's usage is never visible.
    await _seed_event(user.id, at_offset=timedelta(hours=3, minutes=2), charged=999, status="released")
    await _seed_event(other_user.id, at_offset=timedelta(hours=1, minutes=5), charged=5000)

    response = await client.get(
        "/usage/hourly", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["period_start"] and body["period_end"]
    buckets = [
        (datetime.fromisoformat(row["hour"]), row["charged_tokens"]) for row in body["data"]
    ]
    assert buckets == [
        (first.replace(minute=0, second=0, microsecond=0), 120),
        (third.replace(minute=0, second=0, microsecond=0), 7),
    ]

    other_body = (
        await client.get(
            "/usage/hourly", headers={"Authorization": f"Bearer {other['access_token']}"}
        )
    ).json()
    assert [row["charged_tokens"] for row in other_body["data"]] == [5000]


async def test_usage_hourly_requires_auth(client):
    response = await client.get("/usage/hourly")
    assert response.status_code == 401
