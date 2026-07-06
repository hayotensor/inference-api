"""Background maintenance loop for the serving plane.

Responsibilities (each pass):
  * health expiry: miners not seen within ``health_stale_after_seconds`` are
    marked ``unhealthy``;
  * near-expiry re-attestation: ``attested`` miners whose attestation is within
    one maintenance interval of expiry are flagged ``stale`` so the provisioner
    loop re-attests / rotates their token;
  * deregistration: miners not seen for ``dereg_after_seconds`` (and not
    currently attested-and-serving) are marked ``revoked``.

Guarded by ``provisioner_enabled`` at the call site in main.py's lifespan so the
test suite opts in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.ht_indexer import (
    HtIndexerStakeClient,
    StakeQuotaPolicy,
    normalize_stake,
    subnet_stake_allowance,
)
from inference_api.miners.service import HEALTHY, UNHEALTHY
from inference_api.models import CryptoBalanceSnapshot, EVMWallet, Miner, ProvisionedToken, User
from inference_api.security import as_utc, utcnow
from inference_api.usage import UsageService

logger = logging.getLogger(__name__)

# Chain/token labels for the stake allowance snapshot (matches UsageService).
STAKE_CHAIN = "hypertensor"
STAKE_TOKEN_TYPE = "subnet_stake"
# Bound indexer load per maintenance pass; stale wallets beyond this are picked up
# on subsequent passes (staleness is time-based, so nothing is lost).
_MAX_STAKE_REFRESH_PER_PASS = 100


async def expire_health(session: AsyncSession) -> int:
    """Mark miners whose last_seen is too old as unhealthy."""
    now = utcnow()
    cutoff = now - timedelta(seconds=settings.health_stale_after_seconds)
    result = await session.execute(
        select(Miner).where(Miner.health == HEALTHY)
    )
    changed = 0
    for miner in result.scalars():
        if miner.last_seen is None or as_utc(miner.last_seen) <= cutoff:
            miner.health = UNHEALTHY
            session.add(miner)
            changed += 1
    return changed


async def flag_near_expiry(session: AsyncSession) -> int:
    """Flag attested miners near attestation expiry as stale (triggers re-attest)."""
    now = utcnow()
    horizon = now + timedelta(seconds=settings.maintenance_interval_seconds)
    result = await session.execute(
        select(Miner).where(
            Miner.attestation_status == "attested",
            Miner.attestation_expiry.is_not(None),
            Miner.attestation_expiry <= horizon,
        )
    )
    changed = 0
    for miner in result.scalars():
        miner.attestation_status = "stale"
        session.add(miner)
        changed += 1
    return changed


async def expire_tokens(session: AsyncSession) -> int:
    """Revoke provisioned tokens past their expiry."""
    now = utcnow()
    result = await session.execute(
        select(ProvisionedToken).where(
            ProvisionedToken.status == "active",
            ProvisionedToken.expires_at.is_not(None),
            ProvisionedToken.expires_at <= now,
        )
    )
    changed = 0
    for token in result.scalars():
        token.status = "revoked"
        session.add(token)
        changed += 1
    return changed


async def deregister_stale(session: AsyncSession) -> int:
    """Revoke miners not seen for dereg_after_seconds."""
    now = utcnow()
    cutoff = now - timedelta(seconds=settings.dereg_after_seconds)
    result = await session.execute(
        select(Miner).where(Miner.attestation_status != "revoked")
    )
    changed = 0
    for miner in result.scalars():
        if miner.last_seen is not None and as_utc(miner.last_seen) <= cutoff:
            miner.attestation_status = "revoked"
            miner.health = UNHEALTHY
            session.add(miner)
            changed += 1
    return changed


async def _refresh_wallet_stake(
    session: AsyncSession,
    client: HtIndexerStakeClient,
    policy: StakeQuotaPolicy,
    wallet: EVMWallet,
    now,
) -> None:
    """Write a fresh subnet_stake snapshot for one wallet and recalc its period.

    Indexer failures are fail-safe: we write an errored snapshot (allowance 0)
    which ``_latest_crypto_allowance`` ignores, so a transient outage never
    revokes a staker — the prior good allowance stands until a clean read.
    """
    user = await session.get(User, wallet.user_id)
    if user is None:
        return
    allowance = 0
    raw_amount = 0
    normalized = Decimal("0")
    error_message: str | None = None
    try:
        status = await client.get_subnet_stake_status(wallet.address)
        raw_amount = status.raw_amount
        normalized = normalize_stake(status.raw_amount, policy.decimals)
        allowance = subnet_stake_allowance(status, policy, now=now)
    except Exception as exc:  # noqa: BLE001 - indexer errors are fail-safe
        error_message = str(exc)[:512]
        logger.warning("subnet_stake.refresh_error address=%s err=%s", wallet.address, error_message)
    session.add(
        CryptoBalanceSnapshot(
            id=uuid.uuid4(),
            user_id=user.id,
            wallet_address=wallet.address,
            chain=STAKE_CHAIN,
            token_type=STAKE_TOKEN_TYPE,
            raw_balance=str(raw_amount),
            normalized_balance=normalized,
            inference_token_allowance=allowance,
            error_message=error_message,
            checked_at=now,
        )
    )
    await session.flush()
    await UsageService(session).recalculate_current_period(user)


async def refresh_subnet_stake_allowances(session: AsyncSession) -> int:
    """Refresh stale subnet-stake allowance snapshots from the ht-indexer.

    For each linked wallet whose latest ``hypertensor/subnet_stake`` snapshot is
    missing or older than ``subnet_stake_refresh_interval_seconds``, read the
    indexer and rewrite the snapshot + recalc the period. This is how a newly
    30-day-eligible staker gains quota and how an unstake revokes it (bounded by
    the refresh interval).
    """
    if not settings.subnet_stake_quota_enabled:
        return 0
    try:
        client = HtIndexerStakeClient.from_settings()
        policy = StakeQuotaPolicy.from_settings()
    except ValueError:
        logger.exception("subnet_stake.config_error")
        return 0

    now = utcnow()
    cutoff = now - timedelta(seconds=settings.subnet_stake_refresh_interval_seconds)
    wallets = list((await session.execute(select(EVMWallet))).scalars())
    refreshed = 0
    for wallet in wallets:
        if refreshed >= _MAX_STAKE_REFRESH_PER_PASS:
            logger.info("subnet_stake.refresh_capped at=%d", _MAX_STAKE_REFRESH_PER_PASS)
            break
        latest = (
            await session.execute(
                select(CryptoBalanceSnapshot)
                .where(
                    CryptoBalanceSnapshot.user_id == wallet.user_id,
                    CryptoBalanceSnapshot.chain == STAKE_CHAIN,
                    CryptoBalanceSnapshot.token_type == STAKE_TOKEN_TYPE,
                )
                .order_by(CryptoBalanceSnapshot.checked_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None and as_utc(latest.checked_at) > cutoff:
            continue
        await _refresh_wallet_stake(session, client, policy, wallet, now)
        refreshed += 1
    return refreshed


async def run_maintenance_pass() -> dict[str, int]:
    async with async_session_maker() as session:
        stats = {
            "health_expired": await expire_health(session),
            "near_expiry_flagged": await flag_near_expiry(session),
            "tokens_expired": await expire_tokens(session),
            "deregistered": await deregister_stale(session),
        }
        await session.commit()
    # Stake-quota refresh runs in its own session so an indexer/DB hiccup can't
    # roll back the miner-maintenance above (and vice versa).
    if settings.subnet_stake_quota_enabled:
        async with async_session_maker() as session:
            try:
                stats["subnet_stake_refreshed"] = await refresh_subnet_stake_allowances(session)
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("subnet_stake.refresh_pass_error")
                await session.rollback()
                stats["subnet_stake_refreshed"] = 0
    return stats


class MaintenanceLoop:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        interval = settings.maintenance_interval_seconds
        while not self._stop.is_set():
            try:
                await run_maintenance_pass()
            except Exception:  # noqa: BLE001
                logger.exception("maintenance.pass_error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
