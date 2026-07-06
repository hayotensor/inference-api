"""Subnet-stake weekly quota: eligibility, allowance, weekly window, refresh loop."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.ht_indexer import (
    HtIndexerStakeClient,
    StakeQuotaPolicy,
    SubnetStakeStatus,
    compute_streak_start,
    subnet_stake_allowance,
)
from inference_api.maintenance import tasks
from inference_api.models import CryptoBalanceSnapshot, EVMWallet, UsagePeriod, User
from inference_api.security import utcnow
from inference_api.usage import UsageService, _weekly_period

TOKEN = 10**18  # one native token in base units (18 decimals)
ADDR = "0x00000000000000000000000000000000000000ab"
SUBNET = 7

POLICY = StakeQuotaPolicy(
    subnet_id=SUBNET,
    min_days=30,
    min_amount=0,
    decimals=18,
    unit=1,
    per_unit=1000,
    cap=0,
)


def _event(method: str, args: list | dict, *, days_ago: float) -> dict:
    return {
        "method": method,
        "data": json.dumps(args),
        "timestamp": (utcnow() - timedelta(days=days_ago)).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Pure eligibility / allowance
# --------------------------------------------------------------------------- #


def test_allowance_proportional_when_eligible():
    status = SubnetStakeStatus(
        address=ADDR, subnet_id=SUBNET, raw_amount=5 * TOKEN, streak_start=utcnow() - timedelta(days=40)
    )
    assert subnet_stake_allowance(status, POLICY, now=utcnow()) == 5000  # 5 units * 1000


def test_allowance_scales_with_stake():
    now = utcnow()
    small = SubnetStakeStatus(ADDR, SUBNET, 2 * TOKEN, now - timedelta(days=40))
    large = SubnetStakeStatus(ADDR, SUBNET, 9 * TOKEN, now - timedelta(days=40))
    assert subnet_stake_allowance(small, POLICY, now=now) == 2000
    assert subnet_stake_allowance(large, POLICY, now=now) == 9000


def test_allowance_zero_when_streak_too_young():
    status = SubnetStakeStatus(ADDR, SUBNET, 5 * TOKEN, utcnow() - timedelta(days=29))
    assert subnet_stake_allowance(status, POLICY, now=utcnow()) == 0


def test_allowance_zero_when_not_currently_staked():
    status = SubnetStakeStatus(ADDR, SUBNET, 0, utcnow() - timedelta(days=100))
    assert subnet_stake_allowance(status, POLICY, now=utcnow()) == 0


def test_allowance_zero_when_streak_unknown():
    status = SubnetStakeStatus(ADDR, SUBNET, 5 * TOKEN, streak_start=None)
    assert subnet_stake_allowance(status, POLICY, now=utcnow()) == 0


def test_allowance_respects_min_amount():
    policy = StakeQuotaPolicy(SUBNET, 30, min_amount=10, decimals=18, unit=1, per_unit=1000, cap=0)
    status = SubnetStakeStatus(ADDR, SUBNET, 5 * TOKEN, utcnow() - timedelta(days=40))
    assert subnet_stake_allowance(status, policy, now=utcnow()) == 0


def test_allowance_respects_cap():
    policy = StakeQuotaPolicy(SUBNET, 30, min_amount=0, decimals=18, unit=1, per_unit=1000, cap=4000)
    status = SubnetStakeStatus(ADDR, SUBNET, 9 * TOKEN, utcnow() - timedelta(days=40))
    assert subnet_stake_allowance(status, policy, now=utcnow()) == 4000


# --------------------------------------------------------------------------- #
# Streak reconstruction from the event log
# --------------------------------------------------------------------------- #


def test_streak_from_single_add():
    events = [_event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(5 * TOKEN)], days_ago=40)]
    start = compute_streak_start(events, subnet_id=SUBNET, address=ADDR)
    assert start is not None
    assert (utcnow() - start).days >= 39


def test_streak_continuous_keeps_earliest():
    events = [
        _event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(5 * TOKEN)], days_ago=40),
        _event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(2 * TOKEN)], days_ago=10),
    ]
    start = compute_streak_start(events, subnet_id=SUBNET, address=ADDR)
    assert (utcnow() - start).days >= 39  # streak still anchored at the first add


def test_streak_resets_after_full_unstake():
    events = [
        _event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(5 * TOKEN)], days_ago=40),
        _event("SubnetDelegateStakeRemoved", [str(SUBNET), ADDR, str(5 * TOKEN)], days_ago=20),
        _event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(3 * TOKEN)], days_ago=5),
    ]
    start = compute_streak_start(events, subnet_id=SUBNET, address=ADDR)
    assert start is not None
    assert (utcnow() - start).days < 30  # broken streak -> only 5 days old

    status = SubnetStakeStatus(ADDR, SUBNET, 3 * TOKEN, start)
    assert subnet_stake_allowance(status, POLICY, now=utcnow()) == 0


def test_streak_none_when_no_events():
    assert compute_streak_start([], subnet_id=SUBNET, address=ADDR) is None


def test_streak_ignores_other_subnets_and_addresses():
    events = [
        _event("SubnetDelegateStakeAdded", ["999", ADDR, str(5 * TOKEN)], days_ago=40),  # other subnet
        _event("SubnetDelegateStakeAdded", [str(SUBNET), "0xdead", str(5 * TOKEN)], days_ago=40),  # other addr
    ]
    assert compute_streak_start(events, subnet_id=SUBNET, address=ADDR) is None


def test_streak_case_insensitive_address():
    events = [_event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(5 * TOKEN)], days_ago=40)]
    start = compute_streak_start(events, subnet_id=SUBNET, address=ADDR.upper())
    assert start is not None


def test_streak_swap_into_subnet_counts_as_start():
    # SubnetDelegateStakeSwapped(from_subnet, to_subnet, account, amount) moving INTO our subnet.
    events = [_event("SubnetDelegateStakeSwapped", ["9", str(SUBNET), ADDR, str(4 * TOKEN)], days_ago=45)]
    start = compute_streak_start(events, subnet_id=SUBNET, address=ADDR)
    assert start is not None and (utcnow() - start).days >= 44


def test_streak_swap_out_of_subnet_breaks():
    events = [
        _event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(4 * TOKEN)], days_ago=45),
        _event("SubnetDelegateStakeSwapped", [str(SUBNET), "9", ADDR, str(4 * TOKEN)], days_ago=10),
    ]
    # fully swapped out -> balance 0 -> streak cleared
    assert compute_streak_start(events, subnet_id=SUBNET, address=ADDR) is None


# --------------------------------------------------------------------------- #
# Weekly window
# --------------------------------------------------------------------------- #


def test_weekly_period_is_seven_days_on_anchor_weekday():
    now = utcnow()
    start, end = _weekly_period(now)
    assert end - start == timedelta(days=7)
    assert start.weekday() == settings.token_reset_weekday
    assert start.hour == start.minute == start.second == 0
    assert start <= now < end


# --------------------------------------------------------------------------- #
# GraphQL client parsing (mocked transport)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_client_parses_stake_and_streak():
    added = _event("SubnetDelegateStakeAdded", [str(SUBNET), ADDR, str(6 * TOKEN)], days_ago=50)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body["query"]
        if "stakes(" in query:
            return httpx.Response(200, json={"data": {"stakes": {"nodes": [{"amount": str(6 * TOKEN)}]}}})
        return httpx.Response(200, json={"data": {"events": {"nodes": [added]}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = HtIndexerStakeClient(
            graphql_url="http://indexer.test/graphql", subnet_id=SUBNET, client=http
        )
        status = await client.get_subnet_stake_status(ADDR.upper())

    assert status.raw_amount == 6 * TOKEN
    assert status.address == ADDR  # lowercased
    assert status.streak_start is not None
    assert subnet_stake_allowance(status, POLICY, now=utcnow()) == 6000


# --------------------------------------------------------------------------- #
# Integration: allowance flows into the usage gate
# --------------------------------------------------------------------------- #


async def _seed_user(session, *, created_days_ago: int = 60) -> User:
    user = User(id=uuid.uuid4(), is_active=True, created_at=utcnow() - timedelta(days=created_days_ago))
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_stake_snapshot_flows_into_remaining_and_gate():
    async with async_session_maker() as session:
        user = await _seed_user(session)
        session.add(
            CryptoBalanceSnapshot(
                id=uuid.uuid4(),
                user_id=user.id,
                wallet_address=ADDR,
                chain="hypertensor",
                token_type="subnet_stake",
                raw_balance=str(5 * TOKEN),
                normalized_balance=Decimal(5),
                inference_token_allowance=5000,
                error_message=None,
                checked_at=utcnow(),
            )
        )
        await session.flush()

        service = UsageService(session)
        period = await service.recalculate_current_period(user)
        assert period.subnet_stake_allowance == 5000
        assert period.remaining_tokens == 5000

        # Boundary: a reservation for the full allowance is admitted...
        event = await service.reserve_inference_tokens(
            user,
            api_key_id=uuid.uuid4(),
            model="unit-test-model-1x",
            prompt_tokens=3000,
            max_completion_tokens=2000,
        )
        assert event.reserved_tokens == 5000
        # ...and one token beyond it is rejected.
        with pytest.raises(Exception):
            await service.reserve_inference_tokens(
                user,
                api_key_id=uuid.uuid4(),
                model="unit-test-model-1x",
                prompt_tokens=1,
                max_completion_tokens=0,
                request_id="over-limit",
            )


@pytest.mark.asyncio
async def test_errored_snapshot_does_not_revoke_prior_allowance():
    async with async_session_maker() as session:
        user = await _seed_user(session)
        good = CryptoBalanceSnapshot(
            id=uuid.uuid4(), user_id=user.id, wallet_address=ADDR, chain="hypertensor",
            token_type="subnet_stake", raw_balance=str(5 * TOKEN), normalized_balance=Decimal(5),
            inference_token_allowance=5000, error_message=None, checked_at=utcnow() - timedelta(minutes=10),
        )
        errored = CryptoBalanceSnapshot(
            id=uuid.uuid4(), user_id=user.id, wallet_address=ADDR, chain="hypertensor",
            token_type="subnet_stake", raw_balance="0", normalized_balance=Decimal(0),
            inference_token_allowance=0, error_message="ht-indexer HTTP 503", checked_at=utcnow(),
        )
        session.add_all([good, errored])
        await session.flush()
        period = await UsageService(session).recalculate_current_period(user)
        assert period.subnet_stake_allowance == 5000  # latest *non-errored* wins


# --------------------------------------------------------------------------- #
# Maintenance refresh loop
# --------------------------------------------------------------------------- #


@pytest.fixture
def enable_stake_quota(monkeypatch):
    monkeypatch.setattr(settings, "subnet_stake_quota_enabled", True)
    monkeypatch.setattr(settings, "subnet_stake_subnet_id", SUBNET)
    monkeypatch.setattr(settings, "subnet_stake_decimals", 18)
    monkeypatch.setattr(settings, "subnet_stake_min_days", 30)
    monkeypatch.setattr(settings, "subnet_stake_min_amount", 0)
    monkeypatch.setattr(settings, "subnet_stake_tokens_unit", 1)
    monkeypatch.setattr(settings, "subnet_stake_tokens_per_unit", 1000)
    monkeypatch.setattr(settings, "subnet_stake_weekly_token_cap", 0)
    monkeypatch.setattr(settings, "subnet_stake_refresh_interval_seconds", 0)  # always stale -> refresh


@pytest.mark.asyncio
async def test_refresh_loop_grants_then_revokes(monkeypatch, enable_stake_quota):
    holder = {
        "status": SubnetStakeStatus(ADDR, SUBNET, 5 * TOKEN, utcnow() - timedelta(days=40))
    }

    class FakeClient:
        @classmethod
        def from_settings(cls, client=None):
            return cls()

        async def get_subnet_stake_status(self, address):
            return holder["status"]

    monkeypatch.setattr(tasks, "HtIndexerStakeClient", FakeClient)

    async with async_session_maker() as session:
        user = await _seed_user(session)
        session.add(EVMWallet(id=uuid.uuid4(), user_id=user.id, address=ADDR))
        await session.flush()

        # Pass 1: continuously staked >= 30d -> quota granted.
        assert await tasks.refresh_subnet_stake_allowances(session) == 1
        period = await session.get(UsagePeriod, (await UsageService(session).current_period(user)).id)
        assert period.subnet_stake_allowance == 5000

        # Pass 2: wallet has unstaked -> quota revoked.
        holder["status"] = SubnetStakeStatus(ADDR, SUBNET, 0, None)
        assert await tasks.refresh_subnet_stake_allowances(session) == 1
        period = await UsageService(session).recalculate_current_period(user)
        assert period.subnet_stake_allowance == 0
        assert period.remaining_tokens == 0


@pytest.mark.asyncio
async def test_refresh_loop_noop_when_disabled():
    async with async_session_maker() as session:
        user = await _seed_user(session)
        session.add(EVMWallet(id=uuid.uuid4(), user_id=user.id, address=ADDR))
        await session.flush()
        # settings.subnet_stake_quota_enabled is False by default here.
        assert await tasks.refresh_subnet_stake_allowances(session) == 0
