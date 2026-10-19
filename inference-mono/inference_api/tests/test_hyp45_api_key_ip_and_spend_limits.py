"""HYP-45 follow-ups: per-key IP allowlist and credit/USD spend caps (serving plane).

* IP allowlist: ``APIKeyService.authenticate_key`` -> ``key_limits.check_ip_allowed``
  against ``client_ip.resolve_client_ip``, which only honors X-Forwarded-For /
  CF-Connecting-IP from a configured trusted proxy hop.
* Spend caps: ``UsageService.reserve_inference_tokens`` ->
  ``key_limits.enforce_spend_limits``, computed from ``inference_usage_events``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.key_limits import ip_in_allowlist, limit_window
from inference_api.main import create_app
from inference_api.models import APIKey, InferenceUsageEvent, ManualTokenAdjustment, User, UserWallet
from inference_api.security import utcnow
from inference_api.usage import UsageService


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


def _client_from(peer_ip: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app(), client=(peer_ip, 4321)),
        base_url="http://testserver",
    )


async def _models_status(peer_ip: str, raw_key: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    async with _client_from(peer_ip) as client:
        response = await client.get("/v1/models", headers={"X-API-Key": raw_key, **(headers or {})})
    return response.status_code, response.json()


# --------------------------------------------------------------------------- #
# IP allowlist
# --------------------------------------------------------------------------- #


def test_ip_in_allowlist_matches_literals_cidrs_and_mapped_ipv4():
    allowed = ["203.0.113.5", "10.0.0.0/8", "2001:db8::/32"]
    assert ip_in_allowlist("203.0.113.5", allowed)
    assert ip_in_allowlist("10.20.30.40", allowed)
    assert ip_in_allowlist("2001:db8::1", allowed)
    assert ip_in_allowlist("::ffff:10.1.2.3", allowed)
    assert not ip_in_allowlist("203.0.113.6", allowed)
    assert not ip_in_allowlist(None, allowed)
    assert not ip_in_allowlist("not-an-ip", allowed)


async def test_unrestricted_key_is_usable_from_any_ip(seed_api_key):
    seeded = await seed_api_key(allowed_ips=None)
    status, _ = await _models_status("198.51.100.77", seeded.raw_key)
    assert status == 200


async def test_allowlisted_ip_and_cidr_are_accepted(seed_api_key):
    seeded = await seed_api_key(allowed_ips=["198.51.100.7", "192.0.2.0/24"])
    assert (await _models_status("198.51.100.7", seeded.raw_key))[0] == 200
    assert (await _models_status("192.0.2.200", seeded.raw_key))[0] == 200


async def test_non_allowlisted_ip_is_rejected(seed_api_key):
    seeded = await seed_api_key(allowed_ips=["198.51.100.7"])
    status, body = await _models_status("198.51.100.8", seeded.raw_key)
    assert status == 403
    assert body["detail"]["code"] == "ip_not_allowed"


async def test_rejected_ip_does_not_touch_last_used_at(seed_api_key):
    seeded = await seed_api_key(allowed_ips=["198.51.100.7"])
    await _models_status("198.51.100.8", seeded.raw_key)
    async with async_session_maker() as session:
        assert (await session.get(APIKey, seeded.api_key_id)).last_used_at is None


async def test_spoofed_forwarding_headers_are_ignored_without_trusted_proxy(seed_api_key, monkeypatch):
    """With no trusted proxy configured, a client can't claim an allowlisted IP
    by sending X-Forwarded-For / CF-Connecting-IP itself."""
    monkeypatch.setattr(settings, "trusted_proxy_ips", [])
    monkeypatch.setattr(settings, "trust_cf_connecting_ip", True)
    seeded = await seed_api_key(allowed_ips=["203.0.113.5"])
    status, _ = await _models_status(
        "198.51.100.9",
        seeded.raw_key,
        {"X-Forwarded-For": "203.0.113.5", "CF-Connecting-IP": "203.0.113.5"},
    )
    assert status == 403


async def test_spoofed_headers_from_untrusted_peer_are_ignored(seed_api_key, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    monkeypatch.setattr(settings, "trust_cf_connecting_ip", True)
    seeded = await seed_api_key(allowed_ips=["203.0.113.5"])
    status, _ = await _models_status(
        "198.51.100.9",
        seeded.raw_key,
        {"X-Forwarded-For": "203.0.113.5", "CF-Connecting-IP": "203.0.113.5"},
    )
    assert status == 403


async def test_x_forwarded_for_from_trusted_proxy_resolves_real_client(seed_api_key, monkeypatch):
    """Trusted proxy appends the real peer; a client-prepended entry is skipped."""
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    seeded = await seed_api_key(allowed_ips=["203.0.113.5"])
    ok, _ = await _models_status("127.0.0.1", seeded.raw_key, {"X-Forwarded-For": "6.6.6.6, 203.0.113.5"})
    assert ok == 200
    spoofed, _ = await _models_status("127.0.0.1", seeded.raw_key, {"X-Forwarded-For": "203.0.113.5, 6.6.6.6"})
    assert spoofed == 403


async def test_cf_connecting_ip_from_trusted_proxy_when_enabled(seed_api_key, monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["127.0.0.1"])
    seeded = await seed_api_key(allowed_ips=["203.0.113.5"])

    monkeypatch.setattr(settings, "trust_cf_connecting_ip", False)
    off, _ = await _models_status("127.0.0.1", seeded.raw_key, {"CF-Connecting-IP": "203.0.113.5"})
    assert off == 403

    monkeypatch.setattr(settings, "trust_cf_connecting_ip", True)
    on, _ = await _models_status(
        "127.0.0.1", seeded.raw_key, {"CF-Connecting-IP": "203.0.113.5", "X-Forwarded-For": "6.6.6.6"}
    )
    assert on == 200


async def test_ip_restricted_key_fails_closed_on_router_plane(client, seed_api_key, seed_router_token):
    """The router plane only sees the subnet router's address, never the end
    client's, so an IP-restricted user key must not be usable through it."""
    router = await seed_router_token()
    seeded = await seed_api_key(credits=1_000, allowed_ips=["127.0.0.1"])
    response = await client.get(
        "/router/inference/validate?model=small-model&input_tokens=1&max_output_tokens=1",
        headers={"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": seeded.raw_key},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ip_not_allowed"


async def test_model_scoped_key_is_enforced_on_router_plane(client, seed_api_key, seed_router_token):
    router = await seed_router_token()
    seeded = await seed_api_key(credits=1_000, allowed_models=["other-model"])
    response = await client.post(
        "/router/inference/reservations",
        headers={"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": seeded.raw_key},
        json={"request_id": "r-1", "model": "small-model", "input_tokens": 1, "max_output_tokens": 1},
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Spend limits
# --------------------------------------------------------------------------- #


async def _seed_key(
    session,
    *,
    allowance: int = 0,
    wallet_balance: int | None = None,
    limit_period: str | None = None,
    credit_limit: int | None = None,
    usd_limit_cents: int | None = None,
) -> tuple[User, APIKey]:
    user = User(id=uuid.uuid4(), is_active=True, created_at=utcnow())
    session.add(user)
    await session.flush()
    key = APIKey(
        id=uuid.uuid4(),
        user_id=user.id,
        hashed_key=uuid.uuid4().hex,
        scopes=["inference:write"],
        rate_limit_per_minute=120,
        limit_period=limit_period,
        credit_limit=credit_limit,
        usd_limit_cents=usd_limit_cents,
    )
    session.add(key)
    if allowance:
        session.add(ManualTokenAdjustment(id=uuid.uuid4(), user_id=user.id, amount=allowance, created_at=utcnow()))
    if wallet_balance is not None:
        session.add(UserWallet(user_id=user.id, balance_tokens=wallet_balance))
    await session.flush()
    return user, key


async def _reserve(service: UsageService, user: User, key: APIKey, tokens: int):
    return await service.reserve_inference_tokens(
        user, api_key_id=key.id, model="small-model", prompt_tokens=tokens, max_completion_tokens=0
    )


def test_limit_window_bounds():
    now = utcnow().replace(year=2026, month=12, day=15, hour=13)
    start, reset = limit_window("daily", now)
    assert (start.day, start.hour, reset.day) == (15, 0, 16)
    start, reset = limit_window("monthly", now)
    assert (start.month, start.day, reset.year, reset.month) == (12, 1, 2027, 1)
    assert limit_window("never", now) == (None, None)


async def test_credit_limit_blocks_reservation_that_would_exceed_it():
    async with async_session_maker() as session:
        user, key = await _seed_key(session, allowance=10_000, limit_period="daily", credit_limit=100)
        service = UsageService(session)
        await _reserve(service, user, key, 60)
        with pytest.raises(Exception) as excinfo:
            await _reserve(service, user, key, 41)
        assert excinfo.value.status_code == 402
        assert excinfo.value.detail["code"] == "api_key_credit_limit_exceeded"
        assert "resets_at" in excinfo.value.detail
        # Exactly up to the cap is fine.
        await _reserve(service, user, key, 40)


async def test_settled_usage_counts_actual_charge_and_released_reservations_do_not():
    async with async_session_maker() as session:
        user, key = await _seed_key(session, allowance=10_000, limit_period="monthly", credit_limit=100)
        service = UsageService(session)
        big = await _reserve(service, user, key, 90)
        await service.settle_inference_usage(big, prompt_tokens=10, completion_tokens=10)  # charged 20
        released = await _reserve(service, user, key, 80)
        await service.release_inference_reservation(released)
        await _reserve(service, user, key, 80)  # 20 settled + 80 = 100, at the cap


async def test_daily_window_ignores_yesterday_but_never_counts_lifetime():
    async with async_session_maker() as session:
        user, daily_key = await _seed_key(session, allowance=10_000, limit_period="daily", credit_limit=100)
        never_key = APIKey(
            id=uuid.uuid4(),
            user_id=user.id,
            hashed_key=uuid.uuid4().hex,
            scopes=["inference:write"],
            rate_limit_per_minute=120,
            limit_period="never",
            credit_limit=100,
        )
        session.add(never_key)
        service = UsageService(session)
        period = await service.current_period(user)
        yesterday = utcnow() - timedelta(days=1, hours=1)
        for key in (daily_key, never_key):
            session.add(
                InferenceUsageEvent(
                    usage_period_id=period.id,
                    user_id=user.id,
                    api_key_id=key.id,
                    request_id=f"old-{key.id}",
                    model="small-model",
                    charged_tokens=90,
                    status="settled",
                    expires_at=yesterday,
                    settled_at=yesterday,
                    created_at=yesterday,
                )
            )
        await session.flush()

        await _reserve(service, user, daily_key, 50)
        with pytest.raises(Exception) as excinfo:
            await _reserve(service, user, never_key, 50)
        assert excinfo.value.status_code == 402
        assert "resets_at" not in excinfo.value.detail


async def test_usd_limit_caps_wallet_spend_only(monkeypatch):
    """$1.00 cap at $10/1M credits = 100k wallet-funded credits. Allowance-funded
    usage is not USD spend and is not blocked by the USD cap."""
    monkeypatch.setattr(settings, "api_key_usd_cents_per_million_credits", 1_000)
    async with async_session_maker() as session:
        user, key = await _seed_key(
            session, allowance=50_000, wallet_balance=1_000_000, limit_period="monthly", usd_limit_cents=100
        )
        service = UsageService(session)
        first = await _reserve(service, user, key, 110_000)  # 50k allowance + 60k wallet = 60c
        assert first.wallet_reserved_tokens == 60_000
        with pytest.raises(Exception) as excinfo:
            await _reserve(service, user, key, 50_000)  # +50k wallet -> $1.10
        assert excinfo.value.status_code == 402
        assert excinfo.value.detail["code"] == "api_key_usd_limit_exceeded"
        await _reserve(service, user, key, 40_000)  # exactly $1.00


async def test_usd_limit_does_not_block_allowance_funded_requests():
    async with async_session_maker() as session:
        user, key = await _seed_key(session, allowance=500_000, limit_period="daily", usd_limit_cents=1)
        service = UsageService(session)
        await _reserve(service, user, key, 300_000)


async def test_keys_without_limits_are_unaffected():
    async with async_session_maker() as session:
        user, key = await _seed_key(session, allowance=10_000)
        await _reserve(UsageService(session), user, key, 9_000)


async def test_credit_limit_enforced_over_http(client, seed_api_key, seed_router_token):
    router = await seed_router_token()
    seeded = await seed_api_key(credits=10_000, limit_period="daily", credit_limit=250)
    headers = {"Authorization": f"Bearer {router.raw_token}", "X-User-API-Key": seeded.raw_key}
    ok = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={"request_id": "r-ok", "model": "small-model", "input_tokens": 100, "max_output_tokens": 100},
    )
    assert ok.status_code == 201, ok.text
    blocked = await client.post(
        "/router/inference/reservations",
        headers=headers,
        json={"request_id": "r-blocked", "model": "small-model", "input_tokens": 50, "max_output_tokens": 50},
    )
    assert blocked.status_code == 402, blocked.text
    assert blocked.json()["detail"]["code"] == "api_key_credit_limit_exceeded"


async def test_credit_limit_enforced_on_v1_inference(client, seed_api_key, seed_miner, seed_provisioned_token):
    """The product API hot path: a reservation (prompt + max_tokens) over the
    key's cap is refused with 402 before any miner is contacted."""
    from inference_api import product_routes
    from test_hyp45_api_key_model_scoping import _seed_attested_miner, _stub_transport

    seeded = await seed_api_key(credits=10_000, limit_period="daily", credit_limit=20)
    await _seed_attested_miner(seed_miner, seed_provisioned_token, model_id="demo-inference-001")
    product_routes.set_forwarder_transport(_stub_transport())
    try:
        ok = await client.post(
            "/v1/inference",
            headers={"X-API-Key": seeded.raw_key},
            json={"model": "demo-inference-001", "prompt": "hello", "max_tokens": 8},
        )
        assert ok.status_code == 200, ok.text
        blocked = await client.post(
            "/v1/inference",
            headers={"X-API-Key": seeded.raw_key},
            json={"model": "demo-inference-001", "prompt": "hello", "max_tokens": 64},
        )
        assert blocked.status_code == 402, blocked.text
        assert blocked.json()["detail"]["code"] == "api_key_credit_limit_exceeded"
    finally:
        product_routes.set_forwarder_transport(None)
