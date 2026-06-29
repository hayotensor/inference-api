"""Miner registry service: upsert-by-hotkey, model inventory, availability.

This service owns the ``miners`` / ``miner_models`` tables. It NEVER verifies the
self-registration signature itself — that is done by the route via
``talaris_contracts.verify_registration_signature`` (the single source of truth).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.models import Miner, MinerModel, ProvisionedToken
from inference_api.security import utcnow

# Health values a miner advertises map onto our stored health enum.
HEALTHY = "healthy"
DEGRADED = "degraded"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"


def health_from_registration_health(health) -> str:
    """Map a contracts ``MinerHealth`` to our stored health string."""
    if health is None:
        return UNKNOWN
    if not getattr(health, "healthy", True):
        return UNHEALTHY
    state = getattr(health, "state", "online")
    if state in {"offline"}:
        return UNHEALTHY
    if state in {"joining"}:
        return DEGRADED
    return HEALTHY


class MinerRegistryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hotkey(self, hotkey: str) -> Miner | None:
        result = await self.session.execute(
            select(Miner).where(Miner.hotkey == hotkey.lower())
        )
        return result.scalar_one_or_none()

    async def get(self, miner_id: uuid.UUID) -> Miner | None:
        return await self.session.get(Miner, miner_id)

    async def upsert_from_registration(
        self,
        reg,
        *,
        subnet_node_id: int | None,
        chain_class: str | None,
        miner_hash: str,
    ) -> Miner:
        """Create or update a miner by hotkey from a verified SelfRegistration.

        Resets ``attestation_status`` to ``pending`` if the TEE endpoint or
        enclave identity changed (those invalidate any prior attestation).
        """
        hotkey = reg.hotkey.lower()
        miner = await self.get_by_hotkey(hotkey)
        now = utcnow()
        health = health_from_registration_health(reg.health)
        if miner is None:
            miner = Miner(
                id=uuid.uuid4(),
                hotkey=hotkey,
                subnet_node_id=subnet_node_id,
                peer_id=reg.peer_id,
                tee_endpoint=reg.tee_endpoint,
                attestation_status="pending",
                miner_hash=miner_hash,
                chain_class=chain_class,
                health=health,
                last_seen=now,
                capacity={},
                registered_at=now,
            )
            self.session.add(miner)
        else:
            endpoint_changed = miner.tee_endpoint != reg.tee_endpoint
            miner.subnet_node_id = subnet_node_id if subnet_node_id is not None else miner.subnet_node_id
            miner.peer_id = reg.peer_id
            miner.tee_endpoint = reg.tee_endpoint
            miner.miner_hash = miner_hash
            if chain_class is not None:
                miner.chain_class = chain_class
            miner.health = health
            miner.last_seen = now
            if endpoint_changed:
                # The attested endpoint moved: any prior attestation no longer
                # binds the live endpoint, so force re-attestation.
                miner.attestation_status = "pending"
                miner.tls_cert_fingerprint = None
                miner.enclave_verify_key = None
                miner.attestation_verified_at = None
                miner.attestation_expiry = None
                await self._revoke_active_tokens(miner.id)
            self.session.add(miner)
        await self.session.flush()
        await self.replace_models(miner, reg.hosted_models)
        return miner

    async def replace_models(self, miner: Miner, hosted_models) -> None:
        """Replace a miner's advertised models with the registration set."""
        await self.session.execute(
            delete(MinerModel).where(MinerModel.miner_id == miner.id)
        )
        now = utcnow()
        seen: set[tuple[str, str | None]] = set()
        for hm in hosted_models or []:
            key = (hm.model_id, hm.model_version)
            if key in seen:
                continue
            seen.add(key)
            self.session.add(
                MinerModel(
                    id=uuid.uuid4(),
                    miner_id=miner.id,
                    model_id=hm.model_id,
                    model_version=hm.model_version,
                    tee_endpoint=getattr(hm, "tee_endpoint", None),
                    status=getattr(hm, "status", "loaded"),
                    loaded=bool(getattr(hm, "loaded", True)),
                    last_advertised_at=now,
                )
            )
        await self.session.flush()

    async def record_heartbeat(self, miner: Miner, *, health: str, capacity: dict) -> Miner:
        miner.last_seen = utcnow()
        miner.health = health
        if capacity:
            miner.capacity = capacity
        self.session.add(miner)
        await self.session.flush()
        return miner

    async def _revoke_active_tokens(self, miner_id: uuid.UUID) -> None:
        result = await self.session.execute(
            select(ProvisionedToken).where(
                ProvisionedToken.miner_id == miner_id,
                ProvisionedToken.status == "active",
            )
        )
        for token in result.scalars():
            token.status = "revoked"
            self.session.add(token)
        await self.session.flush()

    async def models_for_miner(self, miner_id: uuid.UUID) -> list[str]:
        result = await self.session.execute(
            select(MinerModel.model_id).where(MinerModel.miner_id == miner_id)
        )
        return sorted({row[0] for row in result.all()})

    async def list_available_models(self) -> list[str]:
        """Distinct loaded model ids on attested + healthy miners with an active token."""
        statement = (
            select(MinerModel.model_id)
            .join(Miner, Miner.id == MinerModel.miner_id)
            .join(
                ProvisionedToken,
                (ProvisionedToken.miner_id == Miner.id)
                & (ProvisionedToken.status == "active"),
            )
            .where(
                MinerModel.loaded.is_(True),
                Miner.attestation_status == "attested",
                Miner.health == HEALTHY,
            )
            .distinct()
        )
        result = await self.session.execute(statement)
        return sorted({row[0] for row in result.all()})
