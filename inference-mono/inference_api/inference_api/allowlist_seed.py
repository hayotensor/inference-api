"""Default platform model-allowlist seed (the WS-A tail).

Mirrors ``pricing.DEFAULT_MODEL_PRICING`` / ``seed_default_model_pricing``: a
config-driven baseline that is idempotently upserted into the ``model_allowlist``
table so a fresh deployment serves a non-empty, signed allowlist. Operator edits
to existing rows always win — seeding only inserts entries that are missing.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.models import ModelAllowlist


@dataclass(frozen=True)
class AllowlistSeed:
    model_id: str
    model_hash: str
    model_version: str | None = None
    args_hash: str | None = None
    gpu_hash: str | None = None
    label: str | None = None


# The platform-approved ``(model_id -> model_hash)`` artifacts a brand-new
# deployment ships with. Keep this aligned with the demo models advertised by the
# product API; extend/override via the ``model_allowlist`` table at runtime. May
# be set to ``[]`` to ship an empty (but still validly signed) allowlist.
DEFAULT_MODEL_ALLOWLIST: list[AllowlistSeed] = [
    AllowlistSeed(
        model_id="demo-chat-001",
        model_version="v1",
        model_hash="0" * 64,
        label="demo",
    ),
]


async def seed_default_model_allowlist(session: AsyncSession) -> None:
    """Idempotently insert any missing default allowlist entries.

    Mirrors ``seed_default_model_pricing``: keyed on the table's natural unique
    key ``(model_id, model_version, model_hash)``, insert each default that is
    absent and leave existing rows untouched. Flushes (does not commit) so the
    caller controls the transaction boundary.
    """
    for seed in DEFAULT_MODEL_ALLOWLIST:
        result = await session.execute(
            select(ModelAllowlist).where(
                ModelAllowlist.model_id == seed.model_id,
                ModelAllowlist.model_version == seed.model_version,
                ModelAllowlist.model_hash == seed.model_hash,
            )
        )
        if result.scalar_one_or_none() is None:
            session.add(
                ModelAllowlist(
                    model_id=seed.model_id,
                    model_version=seed.model_version,
                    model_hash=seed.model_hash,
                    args_hash=seed.args_hash,
                    gpu_hash=seed.gpu_hash,
                    label=seed.label,
                    active=True,
                )
            )
    await session.flush()
