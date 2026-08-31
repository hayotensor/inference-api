"""Public, platform-signed per-epoch miner statistics endpoints.

``GET /subnet/stats/{epoch}`` serves the ``MinerEpochStatsArtifact`` stored when
that epoch was finalized, byte-for-byte. ``GET /subnet/stats/latest`` resolves the
newest finalized epoch and serves the same.

Intentionally UNAUTHENTICATED, for the same reason as ``/allowlist``: the body is
Ed25519-signed by the platform key, so a validator verifies it against its own
PINNED platform pubkey rather than trusting transport auth. Adding auth here would
add a way to be denied the artifact without adding any integrity.

A 404 means "not finalized yet", which is a normal state for a recent epoch — the
snapshot is only cut after a settle lag. Validators treat it as "come back later",
never as "this miner earned nothing".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import MinerEpochStatsArtifact

from inference_api.db import get_async_session
from inference_api.errors import not_found
from inference_api.stats_service import MinerEpochStatsService

router = APIRouter(prefix="/subnet/stats", tags=["miner-stats"])


@router.get("/latest", response_model=MinerEpochStatsArtifact)
async def get_latest_miner_stats(
    session: AsyncSession = Depends(get_async_session),
) -> MinerEpochStatsArtifact:
    service = MinerEpochStatsService(session)
    epoch = await service.latest_epoch()
    if epoch is None:
        raise not_found("No miner statistics epoch has been finalized yet")
    artifact = await service.get(epoch)
    if artifact is None:
        raise not_found("No miner statistics epoch has been finalized yet")
    return artifact


@router.get("/{epoch}", response_model=MinerEpochStatsArtifact)
async def get_miner_stats(
    epoch: int = Path(ge=0),
    session: AsyncSession = Depends(get_async_session),
) -> MinerEpochStatsArtifact:
    artifact = await MinerEpochStatsService(session).get(epoch)
    if artifact is None:
        raise not_found(f"Miner statistics for epoch {epoch} are not finalized yet")
    return artifact
