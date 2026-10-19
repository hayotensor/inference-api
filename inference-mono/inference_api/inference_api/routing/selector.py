"""MinerSelector: choose ordered miner candidates for a model.

Eligibility: ``attested`` + ``healthy`` miners that host the requested model
(loaded) AND have an ``active`` provisioned token. Ranking: least-loaded first
(capacity.available_concurrent_requests desc, then queue_depth asc), a
platform-observed reputation tie-break, and a small random jitter so
equally-ranked miners share load instead of stampeding the first one.

``available_concurrent_requests``/``queue_depth`` come off the miner's
self-reported ``capacity`` (bounded again here, defensively, on read — see
``_capacity_int``; ``miners.service._sanitize_capacity`` already bounds them
on write, but this re-clamps in case a row was written before that guard
existed, or by any other path). ``reputation`` is NOT taken from
self-reported capacity at all: a miner has no business grading itself, so it
is computed here from the platform's OWN observations
(``stats_recorder``-authored ``MinerRequestStat`` rows — success/failure of
attempts THIS platform actually forwarded) via ``_platform_reputation``. See
that function for the (intentionally small — this is not a full reputation
system) scoring rule.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.models import Miner, MinerModel, MinerRequestStat, ProvisionedToken
from inference_api.miners.service import (
    HEALTHY,
    MAX_AVAILABLE_CONCURRENT_REQUESTS,
    MAX_QUEUE_DEPTH,
)
from inference_api.security import utcnow


def _unix(dt) -> int | None:
    """A (possibly naive, e.g. SQLite) datetime as a unix int, treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


@dataclass(frozen=True)
class MinerCandidate:
    miner_id: object
    miner_model_id: object
    hotkey: str
    # Carried so a recorded attempt (and the epoch snapshot built from it) can name
    # the on-chain node without a second lookup. May be None for a miner that
    # registered without chain gating.
    subnet_node_id: int | None
    model_id: str
    model_version: str | None
    tee_endpoint: str
    tls_cert_fingerprint: str | None
    model_hash: str | None
    # Carried so a served response can be accompanied by a signed attestation
    # receipt (see inference_api.attestation) naming exactly which enclave
    # identity -- verified once, at provisioning time, via
    # talaris_attest.verify_attestation -- produced it.
    enclave_verify_key: str | None
    attestation_mode: str | None
    attestation_verified_at_unix: int | None
    available_concurrent_requests: int
    queue_depth: int
    reputation: float
    # talaris-e2ee/v1: the enclave's attested E2EE public key (hex), or None for a
    # pre-E2EE enclave. Only ever set from the provisioner's verified result.
    e2ee_public_key: str | None = None

    @property
    def sort_key(self) -> tuple:
        # Higher availability first, then shorter queue, then higher reputation.
        return (
            -self.available_concurrent_requests,
            self.queue_depth,
            -self.reputation,
        )


def _capacity_int(capacity: dict, key: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read ``key`` off self-reported ``capacity``, clamped to ``[minimum, maximum]``.

    Defensive, read-side re-clamp: ``miners.service._sanitize_capacity`` already
    bounds these on write, but re-checking here means a row written before that
    guard existed (or by any other path) can't out-rank everyone else by
    self-reporting e.g. ``available_concurrent_requests: 999999`` or a negative
    ``queue_depth``.
    """
    value = (capacity or {}).get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


# Mirrors inference_api.routing.forwarder.OUTCOME_SUCCESS. Not imported from
# there directly: forwarder imports MinerCandidate FROM this module, so an
# import the other way would be circular.
_OUTCOME_SUCCESS = "success"

# Reputation scoring window / bootstrap threshold. Deliberately simple (a flat
# success ratio, no recency weighting/decay) — this is not a full reputation
# system, just enough that self-reported reputation stops being a free win.
_REPUTATION_LOOKBACK = timedelta(hours=1)
_REPUTATION_MIN_SAMPLES = 3
_REPUTATION_NEUTRAL = 1.0


async def _platform_reputation(
    session: AsyncSession, miner_ids: set, *, now=None
) -> dict:
    """Recent success-rate per miner_id, from the platform's OWN forward
    attempts (``stats_recorder``-authored ``MinerRequestStat`` rows) — the
    platform selected and forwarded these requests itself, so this is a
    first-hand measurement, never anything the miner self-reports.

    A miner with fewer than ``_REPUTATION_MIN_SAMPLES`` recent rows gets the
    neutral score: a brand-new (or rarely-used) miner should not be penalized
    for lack of evidence, only a miner with an observed track record of
    failures should rank behind one without.
    """
    if not miner_ids:
        return {}
    cutoff = (now or utcnow()) - _REPUTATION_LOOKBACK
    statement = (
        select(
            MinerRequestStat.miner_id,
            func.count().label("total"),
            func.sum(
                case((MinerRequestStat.outcome == _OUTCOME_SUCCESS, 1), else_=0)
            ).label("successes"),
        )
        .where(
            MinerRequestStat.miner_id.in_(miner_ids),
            MinerRequestStat.created_at >= cutoff,
        )
        .group_by(MinerRequestStat.miner_id)
    )
    result = await session.execute(statement)
    scores: dict = {}
    for miner_id, total, successes in result.all():
        if not total or total < _REPUTATION_MIN_SAMPLES:
            continue
        scores[miner_id] = (successes or 0) / total
    return scores


class MinerSelector:
    def __init__(self, session: AsyncSession, *, rng: random.Random | None = None) -> None:
        self.session = session
        self._rng = rng or random.Random()

    def _eligible_statement(self):
        """The ONE serving-eligibility predicate (plaintext and E2EE alike).

        attested + loaded model-enclave on a healthy miner with an active
        provisioned token for that model. ``select`` and ``select_instance`` both
        build on this so an E2EE invoke can never reach an enclave the plaintext
        path would refuse.
        """
        return (
            select(Miner, MinerModel)
            .join(MinerModel, MinerModel.miner_id == Miner.id)
            .join(
                ProvisionedToken,
                (ProvisionedToken.miner_id == Miner.id)
                & (ProvisionedToken.status == "active")
                & (ProvisionedToken.model_id == MinerModel.model_id),
            )
            .where(
                MinerModel.loaded.is_(True),
                MinerModel.attestation_status == "attested",
                Miner.health == HEALTHY,
            )
        )

    async def select(self, model_id: str, *, e2ee_only: bool = False) -> list[MinerCandidate]:
        """Ranked candidates for ``model_id``. ``e2ee_only`` additionally requires an
        attested E2EE key (``MinerModel.e2ee_public_key``)."""
        statement = self._eligible_statement().where(MinerModel.model_id == model_id)
        if e2ee_only:
            statement = statement.where(MinerModel.e2ee_public_key.is_not(None))
        candidates = await self._candidates(statement)
        # Shuffle first so ties (identical sort_key) break randomly (jitter), then
        # stable-sort by the load ranking.
        self._rng.shuffle(candidates)
        candidates.sort(key=lambda c: c.sort_key)
        return candidates

    async def select_instance(self, miner_model_id) -> MinerCandidate | None:
        """The candidate for ONE model-enclave (``MinerModel.id``) iff it is
        currently eligible under exactly the predicate ``select`` uses."""
        statement = self._eligible_statement().where(MinerModel.id == miner_model_id)
        candidates = await self._candidates(statement)
        return candidates[0] if candidates else None

    async def _candidates(self, statement) -> list[MinerCandidate]:
        result = await self.session.execute(statement)
        rows = result.all()
        reputation_by_miner = await _platform_reputation(
            self.session, {miner.id for miner, _mm in rows}
        )

        candidates: list[MinerCandidate] = []
        seen: set = set()
        for miner, mm in rows:
            if mm.id in seen:
                continue
            seen.add(mm.id)
            capacity = miner.capacity or {}
            candidates.append(
                MinerCandidate(
                    miner_id=miner.id,
                    miner_model_id=mm.id,
                    hotkey=miner.hotkey,
                    subnet_node_id=miner.subnet_node_id,
                    model_id=mm.model_id,
                    model_version=mm.model_version,
                    tee_endpoint=mm.tee_endpoint or miner.tee_endpoint,
                    tls_cert_fingerprint=mm.tls_cert_fingerprint or miner.tls_cert_fingerprint,
                    model_hash=mm.model_hash,
                    enclave_verify_key=mm.enclave_verify_key or miner.enclave_verify_key,
                    attestation_mode=mm.attestation_mode or miner.attestation_mode,
                    attestation_verified_at_unix=_unix(
                        mm.attestation_verified_at or miner.attestation_verified_at
                    ),
                    available_concurrent_requests=_capacity_int(
                        capacity,
                        "available_concurrent_requests",
                        1,
                        minimum=0,
                        maximum=MAX_AVAILABLE_CONCURRENT_REQUESTS,
                    ),
                    queue_depth=_capacity_int(
                        capacity, "queue_depth", 0, minimum=0, maximum=MAX_QUEUE_DEPTH
                    ),
                    reputation=reputation_by_miner.get(miner.id, _REPUTATION_NEUTRAL),
                    # Deliberately NOT falling back to a Miner-level value: the key
                    # belongs to exactly this model-enclave's attestation.
                    e2ee_public_key=mm.e2ee_public_key,
                )
            )
        return candidates
