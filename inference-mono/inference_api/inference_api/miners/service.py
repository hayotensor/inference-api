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

# Bounds on self-reported capacity (see ``_sanitize_capacity``). A miner has no
# legitimate reason to report more than a few hundred concurrent slots or a
# queue that deep; these exist to stop a self-reported number from being a
# routing-hijack primitive (see routing.selector), not to model real hardware
# limits precisely. Not exposed via settings (no config surface owned here) —
# a follow-up could make these operator-tunable.
MAX_AVAILABLE_CONCURRENT_REQUESTS = 1024
MAX_QUEUE_DEPTH = 100_000


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


def _clamp_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _sanitize_capacity(capacity: dict) -> dict:
    """Bound/clean self-reported heartbeat ``capacity`` before it is stored.

    Two things happen here, both because ``routing.selector.MinerSelector``
    ranks candidates directly off this dict, so an unsanitized value is a
    routing-hijack primitive (a miner can otherwise self-report e.g.
    ``available_concurrent_requests: 999999`` / ``queue_depth: -999`` and
    capture all traffic for a model; combined with the C1 ownership hole this
    becomes traffic hijacking against a specific victim miner):

    * ``available_concurrent_requests`` / ``queue_depth`` are clamped to
      ``[0, MAX_*]`` — a negative queue depth or an absurd concurrency number
      is simply invalid, not "very good capacity".
    * ``reputation`` is DROPPED unconditionally. Reputation is a
      platform-observed signal now (see ``routing.selector``, sourced from
      ``stats_recorder``-authored ``MinerRequestStat`` rows) — it must never
      be settable by the miner itself, so heartbeat cannot smuggle it in
      however cleanly a value is presented.

    Any other keys are passed through unchanged (``extra="allow"`` on the
    wire model; we don't know every field a future miner version might add,
    and only the fields the selector actually reads need bounding).
    """
    clean = dict(capacity or {})
    clean.pop("reputation", None)
    if "available_concurrent_requests" in clean:
        clean["available_concurrent_requests"] = _clamp_int(
            clean["available_concurrent_requests"],
            default=0,
            minimum=0,
            maximum=MAX_AVAILABLE_CONCURRENT_REQUESTS,
        )
    if "queue_depth" in clean:
        clean["queue_depth"] = _clamp_int(
            clean["queue_depth"], default=0, minimum=0, maximum=MAX_QUEUE_DEPTH
        )
    return clean


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
            elif miner.attestation_status == "revoked":
                # A miner deregistered for staleness (maintenance.deregister_stale)
                # must be able to come back: it is presenting a FRESH, signature-
                # verified registration for the same endpoint, which is proof of
                # liveness. Without this it stayed "revoked" forever -- the
                # provisioner loop never selects revoked miners, and nothing else
                # clears the flag, so a miner that merely went quiet could never
                # rejoin. Re-attestation still has to succeed before it serves.
                miner.attestation_status = "pending"
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
            miner.capacity = _sanitize_capacity(capacity)
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
