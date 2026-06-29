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
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.miners.service import HEALTHY, UNHEALTHY
from inference_api.models import Miner, ProvisionedToken
from inference_api.security import as_utc, utcnow

logger = logging.getLogger(__name__)


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


async def run_maintenance_pass() -> dict[str, int]:
    async with async_session_maker() as session:
        stats = {
            "health_expired": await expire_health(session),
            "near_expiry_flagged": await flag_near_expiry(session),
            "tokens_expired": await expire_tokens(session),
            "deregistered": await deregister_stale(session),
        }
        await session.commit()
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
