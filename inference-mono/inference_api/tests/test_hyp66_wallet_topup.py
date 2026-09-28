"""HYP-66: pay-as-you-go wallet spend-down (serving plane).

Mirrors ../../api/tests/test_wallet_topup.py's UsageService-level tests against
this plane's own copy (`inference_api/usage.py`, `inference_api/models.py`).
This plane is the one that actually enforces quota on live inference traffic
(see the repo's memory on the two-DBs footgun), so its reserve/settle/release
paths must draw from the wallet -- and refuse to go negative -- exactly the
same way the control plane's copy does. Crediting the wallet from a Stripe
webhook only happens in the control plane; this plane only ever debits/refunds
an existing balance.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.models import ManualTokenAdjustment, User, UserWallet, WalletTransaction
from inference_api.security import utcnow
from inference_api.usage import UsageService


@pytest.fixture(autouse=True)
def zero_free_allowance(monkeypatch):
    monkeypatch.setattr(settings, "free_monthly_token_allowance", 0)


async def _seed_user(session, *, allowance: int = 0, wallet_balance: int | None = None) -> User:
    user = User(id=uuid.uuid4(), is_active=True, created_at=utcnow())
    session.add(user)
    await session.flush()
    if allowance:
        session.add(
            ManualTokenAdjustment(id=uuid.uuid4(), user_id=user.id, amount=allowance, created_at=utcnow())
        )
    if wallet_balance is not None:
        session.add(UserWallet(user_id=user.id, balance_tokens=wallet_balance))
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_spend_draws_from_allowance_before_the_wallet():
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=10_000, wallet_balance=5_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=4_000, max_completion_tokens=0
        )
        assert event.wallet_reserved_tokens == 0

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 5_000


@pytest.mark.asyncio
async def test_spend_overflows_into_the_wallet_once_allowance_is_exhausted():
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=1_000, wallet_balance=5_000)
        service = UsageService(session)

        event = await service.reserve_inference_tokens(
            user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=3_500, max_completion_tokens=0
        )
        assert event.wallet_reserved_tokens == 2_500

        period = await service.current_period(user)
        assert period.used_tokens == 1_000
        assert period.remaining_tokens == 0

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 2_500

        settled = await service.settle_inference_usage(event, prompt_tokens=3_500, completion_tokens=0)
        assert settled.wallet_reserved_tokens == 2_500

        transactions = (
            await session.execute(select(WalletTransaction).where(WalletTransaction.user_id == user.id))
        ).scalars().all()
        assert len(transactions) == 1
        assert transactions[0].kind == "debit"
        assert transactions[0].delta_tokens == -2_500


@pytest.mark.asyncio
async def test_wallet_never_goes_negative_and_still_402s_when_exhausted():
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=1_000, wallet_balance=500)
        service = UsageService(session)

        with pytest.raises(Exception) as excinfo:
            await service.reserve_inference_tokens(
                user, api_key_id=uuid.uuid4(), model="small-model", prompt_tokens=2_000, max_completion_tokens=0
            )
        assert getattr(excinfo.value, "status_code", None) == 402

        wallet = (
            await session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one()
        assert wallet.balance_tokens == 500


@pytest.mark.asyncio
async def test_a_reservation_released_before_settling_refunds_the_wallet():
    async with async_session_maker() as session:
        user = await _seed_user(session, allowance=1_000, wallet_balance=5_000)
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
