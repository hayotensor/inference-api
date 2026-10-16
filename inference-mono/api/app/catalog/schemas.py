from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.catalog.metadata import ModelCategory

ModelAvailability = Literal["operational", "degraded", "down"]


class CatalogModel(BaseModel):
    id: str
    name: str
    label: str | None = None
    provider: str | None = None
    family: str | None = None
    category: ModelCategory
    parameter_size: str | None = None
    # Credits charged per raw token (HYP-36 `model_pricing.token_multiplier`);
    # 1 credit == 1 baseline token of plan allowance.
    token_multiplier: Decimal
    status: ModelAvailability
    healthy_miner_count: int
    registered_miner_count: int


class ModelCatalogResponse(BaseModel):
    generated_at: datetime
    models: list[CatalogModel]
