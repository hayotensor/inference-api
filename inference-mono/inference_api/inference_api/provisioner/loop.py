"""Background provisioner loop.

Periodically scans for miners that need (re-)provisioning — ``pending`` miners,
and ``attested`` miners whose attestation/token is near expiry — and provisions
them. Each miner is provisioned under a per-miner lock (Redis when present, else
an in-process asyncio lock) so concurrent loops / requests don't double-provision
or race the enclave restart detection.

Guarded by ``provisioner_enabled`` (default False) so the test suite opts in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import defaultdict

import redis.asyncio as redis
from sqlalchemy import or_, select

from inference_api.config import settings
from inference_api.db import async_session_maker
from inference_api.models import Miner
from inference_api.provisioner.service import ProvisionError, ProvisionerService
from inference_api.security import utcnow

logger = logging.getLogger(__name__)

_locks: dict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)


@contextlib.asynccontextmanager
async def per_miner_lock(miner_id: uuid.UUID, redis_client: redis.Redis | None):
    """Acquire a per-miner lock (Redis SET NX with TTL if available, else asyncio)."""
    if redis_client is not None:
        key = f"miner-provision-lock:{miner_id}"
        acquired = False
        try:
            acquired = bool(await redis_client.set(key, "1", nx=True, ex=120))
            if not acquired:
                # Someone else holds it; skip this round for this miner.
                yield False
                return
            yield True
        finally:
            if acquired:
                with contextlib.suppress(Exception):
                    await redis_client.delete(key)
        return
    lock = _locks[miner_id]
    async with lock:
        yield True


async def provision_one(miner_id: uuid.UUID, redis_client: redis.Redis | None = None) -> bool:
    """Provision a single miner under its lock. Returns True on success."""
    async with per_miner_lock(miner_id, redis_client) as got_lock:
        if not got_lock:
            return False
        async with async_session_maker() as session:
            service = ProvisionerService(session)
            try:
                await service.provision_miner(miner_id)
                await session.commit()
                return True
            except ProvisionError as exc:
                await session.commit()  # persist the 'failed' status transition
                logger.warning("provisioner.failed", extra={"miner_id": str(miner_id), "error": str(exc)})
                return False
            except Exception:  # noqa: BLE001
                await session.rollback()
                logger.exception("provisioner.unexpected", extra={"miner_id": str(miner_id)})
                return False


async def _candidates() -> list[uuid.UUID]:
    now = utcnow()
    async with async_session_maker() as session:
        result = await session.execute(
            select(Miner.id).where(
                or_(
                    Miner.attestation_status == "pending",
                    Miner.attestation_status == "stale",
                    (Miner.attestation_status == "attested")
                    & (Miner.attestation_expiry.is_not(None))
                    & (Miner.attestation_expiry <= now),
                )
            )
        )
        return [row[0] for row in result.all()]


class ProvisionerLoop:
    def __init__(self, *, redis_client: redis.Redis | None = None) -> None:
        self._redis = redis_client
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run_once(self) -> int:
        provisioned = 0
        for miner_id in await _candidates():
            if await provision_one(miner_id, self._redis):
                provisioned += 1
        return provisioned

    async def _run(self) -> None:
        interval = settings.provisioner_loop_interval_seconds
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001
                logger.exception("provisioner.loop_error")
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
