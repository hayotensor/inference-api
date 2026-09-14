"""HYP-58: public, unauthenticated per-model service status (`GET /status/models`).

A minimal first pass modeled on the existing "operational vs degraded" split
`product_routes.list_models` already establishes for the authenticated
product catalog (`ModelInfo.status`) -- this just makes the same signal
readable WITHOUT an API key, for the dashboard's public `/status` page (and
anyone else who wants it, same trust posture as `/allowlist` and `/attest`).

Status per model is derived purely from what the platform can observe right
now (miner heartbeats -- see `miners.service.MinerRegistryService.
record_heartbeat`/`Miner.health`, and `routing.selector.MinerSelector`'s own
eligibility rule: attested + healthy + loaded + an active provisioned token):

  operational  at least one miner currently eligible to serve it
  degraded     registered by at least one non-terminal (not revoked) miner,
               but none are currently eligible (unattested/unhealthy/stale)
  down         platform-approved (on the signed model allowlist) but no
               miner -- terminal or otherwise -- currently advertises it

This is deliberately NOT a historical-uptime system: no incident timeline, no
uptime percentage, no persistence of past states. It reports live snapshot
availability only, same as a `GET /health`-style check -- a reasonable first
pass per HYP-58's brief ("a pragmatic minimal version"), with the richer
history-over-time view left for later if it's wanted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.db import get_async_session
from inference_api.miners.service import HEALTHY
from inference_api.models import Miner, MinerModel, ModelAllowlist, ProvisionedToken

router = APIRouter(tags=["status"])

ModelStatus = Literal["operational", "degraded", "down"]

# Mirrors product_routes._TERMINAL_MINER_STATES: a revoked miner is gone for
# good, so it shouldn't keep a model reading as merely "degraded" forever.
_TERMINAL_MINER_STATES = ("revoked",)


class ModelStatusEntry(BaseModel):
    model_id: str
    label: str | None = None
    status: ModelStatus
    healthy_miner_count: int
    registered_miner_count: int


class StatusResponse(BaseModel):
    generated_at: datetime
    overall_status: ModelStatus
    models: list[ModelStatusEntry]


async def _registered_miner_counts(session: AsyncSession) -> dict[str, int]:
    """Distinct non-terminal miners advertising each model_id, any health state."""
    statement = (
        select(MinerModel.model_id, func.count(func.distinct(MinerModel.miner_id)))
        .join(Miner, Miner.id == MinerModel.miner_id)
        .where(Miner.attestation_status.notin_(_TERMINAL_MINER_STATES))
        .group_by(MinerModel.model_id)
    )
    result = await session.execute(statement)
    return {model_id: count for model_id, count in result.all()}


async def _healthy_miner_counts(session: AsyncSession) -> dict[str, int]:
    """Distinct miners currently eligible to serve each model_id.

    Same eligibility rule as `MinerSelector.select` / `MinerRegistryService.
    list_available_models`: attested + healthy + loaded + an active
    provisioned token for that model.
    """
    statement = (
        select(MinerModel.model_id, func.count(func.distinct(Miner.id)))
        .join(Miner, Miner.id == MinerModel.miner_id)
        .join(
            ProvisionedToken,
            (ProvisionedToken.miner_id == Miner.id)
            & (ProvisionedToken.status == "active")
            & (ProvisionedToken.model_id == MinerModel.model_id),
        )
        .where(
            MinerModel.loaded.is_(True),
            Miner.attestation_status == "attested",
            Miner.health == HEALTHY,
        )
        .group_by(MinerModel.model_id)
    )
    result = await session.execute(statement)
    return {model_id: count for model_id, count in result.all()}


async def _allowlisted_models(session: AsyncSession) -> dict[str, str | None]:
    """Active platform-approved model_id -> label (e.g. "demo", "observed-attested").

    Included so a model the platform advertises as a product, but which
    currently has zero miners at all (not even a stale/revoked one), shows up
    as "down" instead of silently not appearing on the status page.
    """
    statement = select(ModelAllowlist.model_id, ModelAllowlist.label).where(
        ModelAllowlist.active.is_(True)
    )
    result = await session.execute(statement)
    labels: dict[str, str | None] = {}
    for model_id, label in result.all():
        # First (only) label wins if somehow duplicated across hash variants.
        labels.setdefault(model_id, label)
    return labels


def _status_for(healthy: int, registered: int) -> ModelStatus:
    if healthy > 0:
        return "operational"
    if registered > 0:
        return "degraded"
    return "down"


_OVERALL_SEVERITY: dict[ModelStatus, int] = {"operational": 0, "degraded": 1, "down": 2}


@router.get("/status/models", response_model=StatusResponse)
async def get_model_status(
    session: AsyncSession = Depends(get_async_session),
) -> StatusResponse:
    registered_counts = await _registered_miner_counts(session)
    healthy_counts = await _healthy_miner_counts(session)
    labels = await _allowlisted_models(session)

    model_ids = set(registered_counts) | set(healthy_counts) | set(labels)

    entries = [
        ModelStatusEntry(
            model_id=model_id,
            label=labels.get(model_id),
            status=_status_for(healthy_counts.get(model_id, 0), registered_counts.get(model_id, 0)),
            healthy_miner_count=healthy_counts.get(model_id, 0),
            registered_miner_count=registered_counts.get(model_id, 0),
        )
        for model_id in sorted(model_ids)
    ]

    overall: ModelStatus = "operational"
    for entry in entries:
        if _OVERALL_SEVERITY[entry.status] > _OVERALL_SEVERITY[overall]:
            overall = entry.status

    return StatusResponse(
        generated_at=datetime.now(timezone.utc),
        overall_status=overall,
        models=entries,
    )
