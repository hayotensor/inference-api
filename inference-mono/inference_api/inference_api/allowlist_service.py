"""Build + sign the platform model-allowlist artifact (WS-F).

Reads the ACTIVE ``model_allowlist`` rows, maps each to a
``talaris_contracts.AllowlistEntry``, and signs the canonical artifact with the
SAME platform Ed25519 key used for provisioning (``load_provisioner_signing_key``)
— subnet validators pin that key's pubkey to verify it. The signed ``version``
(and bound ``not_before``) is derived deterministically from the table state so
it is monotonic as the allowlist changes: two reads of an unchanged table sign
the identical body, and any edit (which bumps ``updated_at``) advances it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import (
    AllowlistEntry,
    ModelAllowlistArtifact,
    sign_model_allowlist,
)

from inference_api.allowlist_seed import seed_default_model_allowlist
from inference_api.models import ModelAllowlist
from inference_api.provisioner.key import load_provisioner_signing_key


def _row_unix(dt) -> int:
    """``updated_at`` as a unix int, treating a naive (SQLite) value as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _monotonic_version(rows: Sequence[ModelAllowlist]) -> int:
    """Deterministic, non-decreasing allowlist version.

    The max row ``updated_at`` (as a unix int) — monotonic as the table changes,
    since every insert/update bumps ``updated_at``. Empty table -> version 0.
    """
    if not rows:
        return 0
    return max(_row_unix(row.updated_at) for row in rows)


class ModelAllowlistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _active_rows(self) -> list[ModelAllowlist]:
        result = await self.session.execute(
            select(ModelAllowlist).where(ModelAllowlist.active.is_(True))
        )
        return list(result.scalars().all())

    async def build_artifact(self) -> ModelAllowlistArtifact:
        """Seed defaults (idempotent), read ACTIVE rows, sign the artifact."""
        await seed_default_model_allowlist(self.session)
        rows = await self._active_rows()
        entries = [
            AllowlistEntry(
                model_id=row.model_id,
                model_version=row.model_version,
                model_hash=row.model_hash,
                args_hash=row.args_hash,
                gpu_hash=row.gpu_hash,
                label=row.label,
            )
            for row in rows
        ]
        version = _monotonic_version(rows)
        return sign_model_allowlist(
            entries,
            version,
            load_provisioner_signing_key(),
            not_before=version,
        )
