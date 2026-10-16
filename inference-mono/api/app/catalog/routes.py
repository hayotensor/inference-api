"""HYP-92: public, read-only model catalog (`GET /models`) for the `/models` page.

One row per model the platform knows about -- every model advertised by a
non-revoked miner plus every active allowlist entry (so an approved model with
no host yet still appears, as "down") -- joined with its HYP-36 credit rate and
the same live availability rule as the serving plane's `GET /status/models`
(inference_api/status_routes.py):

  operational  >= 1 miner attested + healthy + loaded + active provisioned token
  degraded     registered by a non-revoked miner, but none currently eligible
  down         allowlisted, no non-revoked miner advertises it

Seeded `model_pricing` rows alone (the placeholder `small-model` etc. catalog)
never create a row: like `/v1/models`, this lists real models only. A model
with no pricing row is shown at 1x, which is exactly what billing charges it
(`ModelPricingService.multiplier_for_model`). Provider/family/category/size
are derived from the id -- see `app/catalog/metadata.py`.

Unauthenticated (same posture as `GET /billing/plans`): it exposes only model
ids, public prices and miner counts already public on `/status`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.metadata import derive_metadata
from app.catalog.schemas import CatalogModel, ModelAvailability, ModelCatalogResponse
from app.core.rate_limit import limiter
from app.db.models.miner import Miner, MinerModel, ProvisionedToken
from app.db.models.model_allowlist import ModelAllowlist
from app.db.models.model_pricing import ModelPricing
from app.db.session import get_async_session

router = APIRouter(prefix="/models", tags=["models"])

# Mirrors inference_api/status_routes.py and app/api_keys/routes.py.
_TERMINAL_MINER_STATES = ("revoked",)
_HEALTHY = "healthy"


async def _registered_counts(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(MinerModel.model_id, func.count(func.distinct(MinerModel.miner_id)))
        .join(Miner, Miner.id == MinerModel.miner_id)
        .where(Miner.attestation_status.notin_(_TERMINAL_MINER_STATES))
        .group_by(MinerModel.model_id)
    )
    return {model_id: count for model_id, count in result.all()}


async def _healthy_counts(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
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
            Miner.health == _HEALTHY,
        )
        .group_by(MinerModel.model_id)
    )
    return {model_id: count for model_id, count in result.all()}


async def _allowlist_labels(session: AsyncSession) -> dict[str, str | None]:
    result = await session.execute(
        select(ModelAllowlist.model_id, ModelAllowlist.label).where(ModelAllowlist.active.is_(True))
    )
    labels: dict[str, str | None] = {}
    for model_id, label in result.all():
        labels.setdefault(model_id, label)
    return labels


async def _multipliers(session: AsyncSession) -> dict[str, Decimal]:
    result = await session.execute(
        select(ModelPricing.model_name, ModelPricing.token_multiplier).where(
            ModelPricing.active.is_(True)
        )
    )
    return {name: Decimal(multiplier) for name, multiplier in result.all()}


def _availability(healthy: int, registered: int) -> ModelAvailability:
    if healthy > 0:
        return "operational"
    if registered > 0:
        return "degraded"
    return "down"


@router.get("", response_model=ModelCatalogResponse)
@limiter.limit("60/minute")
async def list_model_catalog(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> ModelCatalogResponse:
    registered = await _registered_counts(session)
    healthy = await _healthy_counts(session)
    labels = await _allowlist_labels(session)
    multipliers = await _multipliers(session)

    models: list[CatalogModel] = []
    for model_id in sorted(set(registered) | set(labels)):
        meta = derive_metadata(model_id)
        healthy_count = healthy.get(model_id, 0)
        registered_count = registered.get(model_id, 0)
        models.append(
            CatalogModel(
                id=model_id,
                name=meta.name,
                label=labels.get(model_id),
                provider=meta.provider,
                family=meta.family,
                category=meta.category,
                parameter_size=meta.parameter_size,
                token_multiplier=multipliers.get(model_id, Decimal("1")),
                status=_availability(healthy_count, registered_count),
                healthy_miner_count=healthy_count,
                registered_miner_count=registered_count,
            )
        )
    return ModelCatalogResponse(generated_at=datetime.now(timezone.utc), models=models)
