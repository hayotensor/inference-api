"""Per-model usage summary (HYP-8) and its pagination (HYP-32).

Covers: one summed row per model with no double counting (HYP-6 regression),
and limit/offset pagination on both /usage/by-model and /usage/by-model/{model}.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.core.security import expires_in, generate_uuid, utcnow
from app.db.models.inference_usage_event import InferenceUsageEvent
from app.db.models.user import User
from app.db.session import async_session_maker
from app.usage.service import UsageService

PASSWORD = "correct-horse-123"


async def register_and_login(client, email: str) -> dict:
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": email.split("@", 1)[0]},
    )
    assert register.status_code == 201, register.text
    login = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()


async def _user_by_email(email: str) -> User:
    async with async_session_maker() as session:
        return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _seed_settled_event(
    user_id,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    charged_tokens: int,
    request_id: str | None = None,
) -> None:
    """Writes a single settled usage event directly, the way settle_inference_usage
    leaves one behind, without going through the reserve/settle HTTP round trip."""
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        period = await UsageService(session).recalculate_current_period(user)
        now = utcnow()
        event = InferenceUsageEvent(
            usage_period_id=period.id,
            user_id=user_id,
            api_key_id=None,
            request_id=request_id or generate_uuid().hex,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            raw_total_tokens=prompt_tokens + completion_tokens,
            model_multiplier=Decimal("1"),
            reserved_tokens=charged_tokens,
            charged_tokens=charged_tokens,
            status="settled",
            expires_at=expires_in(seconds=3600),
            settled_at=now,
        )
        session.add(event)
        await session.commit()


async def test_usage_by_model_sums_once_per_model_not_once_per_field(client):
    await register_and_login(client, "model-a@example.com")
    login = await client.post(
        "/auth/login", json={"email": "model-a@example.com", "password": PASSWORD}
    )
    access_token = login.json()["access_token"]
    user = await _user_by_email("model-a@example.com")

    # Two requests against model A, one against model B.
    await _seed_settled_event(
        user.id, model="model-a", prompt_tokens=100, completion_tokens=50, charged_tokens=150
    )
    await _seed_settled_event(
        user.id, model="model-a", prompt_tokens=10, completion_tokens=5, charged_tokens=15
    )
    await _seed_settled_event(
        user.id, model="model-b", prompt_tokens=7, completion_tokens=3, charged_tokens=10
    )

    response = await client.get(
        "/usage/by-model", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    rows_by_model = {row["model"]: row for row in body["data"]}
    assert set(rows_by_model) == {"model-a", "model-b"}

    # HYP-6 regression: the prompt is not counted four times. Model A's two
    # events sum exactly, not any multiple of that.
    model_a = rows_by_model["model-a"]
    assert model_a["request_count"] == 2
    assert model_a["prompt_tokens"] == 110
    assert model_a["completion_tokens"] == 55
    assert model_a["charged_tokens"] == 165

    model_b = rows_by_model["model-b"]
    assert model_b["request_count"] == 1
    assert model_b["charged_tokens"] == 10

    # Detail endpoint's summary matches the same totals.
    detail = await client.get(
        "/usage/by-model/model-a", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["summary"]["charged_tokens"] == 165
    assert detail_body["summary"]["request_count"] == 2
    assert len(detail_body["events"]) == 2
    assert detail_body["total_events"] == 2


async def test_usage_by_model_pagination(client):
    await register_and_login(client, "paginate@example.com")
    login = await client.post(
        "/auth/login", json={"email": "paginate@example.com", "password": PASSWORD}
    )
    access_token = login.json()["access_token"]
    user = await _user_by_email("paginate@example.com")

    models = [f"model-{i}" for i in range(5)]
    for model in models:
        await _seed_settled_event(
            user.id, model=model, prompt_tokens=10, completion_tokens=5, charged_tokens=15
        )

    headers = {"Authorization": f"Bearer {access_token}"}

    page1 = await client.get("/usage/by-model?limit=2&offset=0", headers=headers)
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["total"] == 5
    assert body1["limit"] == 2
    assert body1["offset"] == 0
    assert len(body1["data"]) == 2

    page2 = await client.get("/usage/by-model?limit=2&offset=2", headers=headers)
    body2 = page2.json()
    assert body2["offset"] == 2
    assert len(body2["data"]) == 2

    page3 = await client.get("/usage/by-model?limit=2&offset=4", headers=headers)
    body3 = page3.json()
    assert body3["offset"] == 4
    assert len(body3["data"]) == 1

    # No overlap and no gaps across the three pages.
    seen_models = {row["model"] for row in body1["data"] + body2["data"] + body3["data"]}
    assert seen_models == set(models)


async def test_usage_by_model_detail_events_pagination(client):
    await register_and_login(client, "detail-page@example.com")
    login = await client.post(
        "/auth/login", json={"email": "detail-page@example.com", "password": PASSWORD}
    )
    access_token = login.json()["access_token"]
    user = await _user_by_email("detail-page@example.com")

    for i in range(7):
        await _seed_settled_event(
            user.id,
            model="model-x",
            prompt_tokens=1,
            completion_tokens=1,
            charged_tokens=2,
            request_id=f"req-{i}",
        )

    headers = {"Authorization": f"Bearer {access_token}"}
    page1 = await client.get(
        "/usage/by-model/model-x?limit=3&offset=0", headers=headers
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["total_events"] == 7
    assert body1["limit"] == 3
    assert body1["offset"] == 0
    assert len(body1["events"]) == 3
    # Summary totals reflect all 7 events, independent of the page size.
    assert body1["summary"]["request_count"] == 7
    assert body1["summary"]["charged_tokens"] == 14

    page2 = await client.get(
        "/usage/by-model/model-x?limit=3&offset=3", headers=headers
    )
    body2 = page2.json()
    assert len(body2["events"]) == 3
    assert body2["summary"]["request_count"] == 7

    page3 = await client.get(
        "/usage/by-model/model-x?limit=3&offset=6", headers=headers
    )
    body3 = page3.json()
    assert len(body3["events"]) == 1

    request_ids = {e["request_id"] for e in body1["events"] + body2["events"] + body3["events"]}
    assert request_ids == {f"req-{i}" for i in range(7)}


async def test_usage_by_model_detail_unknown_model_is_404(client):
    await register_and_login(client, "no-usage@example.com")
    login = await client.post(
        "/auth/login", json={"email": "no-usage@example.com", "password": PASSWORD}
    )
    access_token = login.json()["access_token"]

    response = await client.get(
        "/usage/by-model/does-not-exist",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 404
