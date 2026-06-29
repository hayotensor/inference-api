"""MinerSelector: choose ordered miner candidates for a model.

Eligibility: ``attested`` + ``healthy`` miners that host the requested model
(loaded) AND have an ``active`` provisioned token. Ranking: least-loaded first
(capacity.available_concurrent_requests desc, then queue_depth asc), a simple
reputation tie-break, and a small random jitter so equally-ranked miners share
load instead of stampeding the first one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.models import Miner, MinerModel, ProvisionedToken
from inference_api.miners.service import HEALTHY


@dataclass(frozen=True)
class MinerCandidate:
    miner_id: object
    miner_model_id: object
    hotkey: str
    model_id: str
    model_version: str | None
    tee_endpoint: str
    tls_cert_fingerprint: str | None
    model_hash: str | None
    available_concurrent_requests: int
    queue_depth: int
    reputation: float

    @property
    def sort_key(self) -> tuple:
        # Higher availability first, then shorter queue, then higher reputation.
        return (
            -self.available_concurrent_requests,
            self.queue_depth,
            -self.reputation,
        )


def _capacity_int(capacity: dict, key: str, default: int) -> int:
    value = (capacity or {}).get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _reputation(capacity: dict) -> float:
    value = (capacity or {}).get("reputation", 1.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


class MinerSelector:
    def __init__(self, session: AsyncSession, *, rng: random.Random | None = None) -> None:
        self.session = session
        self._rng = rng or random.Random()

    async def select(self, model_id: str) -> list[MinerCandidate]:
        statement = (
            select(Miner, MinerModel)
            .join(MinerModel, MinerModel.miner_id == Miner.id)
            .join(
                ProvisionedToken,
                (ProvisionedToken.miner_id == Miner.id)
                & (ProvisionedToken.status == "active")
                & (ProvisionedToken.model_id == MinerModel.model_id),
            )
            .where(
                MinerModel.model_id == model_id,
                MinerModel.loaded.is_(True),
                MinerModel.attestation_status == "attested",
                Miner.health == HEALTHY,
            )
        )
        result = await self.session.execute(statement)
        candidates: list[MinerCandidate] = []
        seen: set = set()
        for miner, mm in result.all():
            if mm.id in seen:
                continue
            seen.add(mm.id)
            capacity = miner.capacity or {}
            candidates.append(
                MinerCandidate(
                    miner_id=miner.id,
                    miner_model_id=mm.id,
                    hotkey=miner.hotkey,
                    model_id=mm.model_id,
                    model_version=mm.model_version,
                    tee_endpoint=mm.tee_endpoint or miner.tee_endpoint,
                    tls_cert_fingerprint=mm.tls_cert_fingerprint or miner.tls_cert_fingerprint,
                    model_hash=mm.model_hash,
                    available_concurrent_requests=_capacity_int(
                        capacity, "available_concurrent_requests", 1
                    ),
                    queue_depth=_capacity_int(capacity, "queue_depth", 0),
                    reputation=_reputation(capacity),
                )
            )
        # Shuffle first so ties (identical sort_key) break randomly (jitter), then
        # stable-sort by the load ranking.
        self._rng.shuffle(candidates)
        candidates.sort(key=lambda c: c.sort_key)
        return candidates
