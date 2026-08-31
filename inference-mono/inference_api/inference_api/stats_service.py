"""Finalize an epoch's miner statistics into an immutable, platform-signed artifact.

This is the hinge of the whole design. Validators must submit IDENTICAL on-chain
score sets (``subnet.consensus.utils.compare_consensus_data`` demands an exact
match), which is only possible if they all score from identical input. So:

* an epoch is finalized ONCE, after a settle lag, from the platform's own records;
* the resulting artifact is signed with the platform key validators already pin for
  ``/allowlist``, and stored verbatim;
* every later read returns those exact bytes.

``finalize`` is therefore idempotent by construction and MUST stay that way. If it
ever re-aggregated an already-published epoch, two validators reading at different
times would score different numbers and consensus would split. A wrong published
epoch is not fixable — it is fixed forward, in a later epoch.

Only ``verified_*`` tokens (corroborated by the enclave's signed receipt chain — see
``inference_api.miners.receipts``) go into the artifact's reward-bearing fields.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import MinerEpochStatsArtifact, MinerStatsEntry, sign_miner_stats

from inference_api.models import Miner, MinerEpochSnapshot, MinerEpochStat, MinerRequestStat
from inference_api.provisioner.key import load_provisioner_signing_key
from inference_api.routing.forwarder import OUTCOME_SUCCESS
from inference_api.security import utcnow

logger = logging.getLogger(__name__)


def percentile_ms(values: Sequence[int], percentile: int) -> int | None:
    """Nearest-rank percentile over measured latencies, or None if there are none.

    Deliberately integer-only and dependency-free: this number is signed into the
    artifact, so it must be reproducible exactly rather than depend on a library's
    interpolation choice.
    """
    ordered = sorted(int(v) for v in values if v is not None)
    if not ordered:
        return None
    # Nearest-rank: index = ceil(p/100 * n) - 1, clamped.
    rank = (int(percentile) * len(ordered) + 99) // 100
    index = min(max(rank - 1, 0), len(ordered) - 1)
    return ordered[index]


def _unix(dt) -> int:
    """A datetime as a unix int, treating a naive (SQLite) value as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class MinerEpochStatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ public

    async def get(self, epoch: int) -> MinerEpochStatsArtifact | None:
        """Return the stored artifact for ``epoch``, or None if not yet finalized.

        Parsed back from the persisted JSON rather than rebuilt, so the signature is
        always checked against — and served with — the bytes that were signed.
        """
        row = await self.session.get(MinerEpochSnapshot, int(epoch))
        if row is None:
            return None
        return MinerEpochStatsArtifact.model_validate(json.loads(row.payload_json))

    async def latest_epoch(self) -> int | None:
        """The highest finalized epoch, or None if nothing has been finalized."""
        return (
            await self.session.execute(select(func.max(MinerEpochSnapshot.epoch)))
        ).scalar_one_or_none()

    async def finalize(self, epoch: int) -> MinerEpochStatsArtifact:
        """Aggregate + sign + persist ``epoch``. Idempotent: a no-op if already done.

        Returns the artifact either way, so callers cannot accidentally distinguish
        (and act differently on) the first and subsequent calls.
        """
        epoch = int(epoch)
        existing = await self.get(epoch)
        if existing is not None:
            return existing

        aggregates = await self._aggregate(epoch)
        await self._persist_aggregates(epoch, aggregates)

        entries = [
            MinerStatsEntry(
                hotkey=row.miner_hotkey,
                subnet_node_id=row.subnet_node_id,
                model_id=row.model_id,
                model_version=row.model_version,
                requests_ok=row.requests_ok,
                requests_failed=row.requests_failed,
                verified_prompt_tokens=row.verified_prompt_tokens,
                verified_completion_tokens=row.verified_completion_tokens,
                verified_total_tokens=row.verified_total_tokens,
                latency_p95_ms=row.latency_p95_ms,
                usage_count_end=row.tee_usage_count_end,
                usage_chain_head_end=row.tee_usage_chain_head_end,
            )
            for row in aggregates
        ]

        # Version = finalization time. Monotonic across epochs and stable for a given
        # epoch (it is stored, never recomputed), which is exactly what consumers'
        # rollback protection needs. ``not_before`` matches: the artifact is valid
        # from the moment it was signed.
        finalized_at = utcnow()
        version = _unix(finalized_at)
        artifact = sign_miner_stats(
            epoch,
            entries,
            version,
            load_provisioner_signing_key(),
            not_before=version,
        )

        payload_json = json.dumps(artifact.model_dump(mode="json"), separators=(",", ":"))
        self.session.add(
            MinerEpochSnapshot(
                epoch=epoch,
                version=version,
                signature=artifact.signature,
                signer_pubkey=artifact.signer_pubkey,
                entry_count=len(entries),
                payload_json=payload_json,
                finalized_at=finalized_at,
            )
        )
        logger.info(
            "miner-stats: finalized epoch=%s entries=%s verified_tokens=%s version=%s",
            epoch,
            len(entries),
            sum(e.verified_total_tokens for e in entries),
            version,
        )
        return artifact

    async def finalize_due(
        self, *, current_epoch: int, settle_lag_epochs: int, max_per_pass: int
    ) -> list[int]:
        """Finalize every epoch that has closed but has no snapshot yet.

        Walks BACKWARD from the newest finalizable epoch so a fresh deployment (or a
        recovery after an outage) publishes the most useful epochs first, and stops
        once it reaches one that is already finalized — everything older than a
        finalized epoch is finalized too, since this is the only writer.

        ``max_per_pass`` bounds the work so catching up degrades gracefully instead
        of stalling the whole maintenance pass; what was left is logged, not hidden.
        """
        newest = int(current_epoch) - 1 - int(settle_lag_epochs)
        if newest < 0:
            return []

        finalized: list[int] = []
        epoch = newest
        while epoch >= 0 and len(finalized) < int(max_per_pass):
            if await self.session.get(MinerEpochSnapshot, epoch) is not None:
                break
            # Only aggregate epochs we actually saw traffic in; empty ones would
            # otherwise walk all the way back to epoch 0 on a fresh install.
            if not await self._has_rows(epoch):
                break
            await self.finalize(epoch)
            finalized.append(epoch)
            epoch -= 1

        if len(finalized) >= int(max_per_pass) and epoch >= 0:
            logger.info(
                "miner-stats: hit max_per_pass=%s; epochs at/below %s deferred to the next pass",
                max_per_pass,
                epoch,
            )
        return finalized

    # ---------------------------------------------------------------- internal

    async def _has_rows(self, epoch: int) -> bool:
        return (
            await self.session.execute(
                select(MinerRequestStat.id).where(MinerRequestStat.epoch == int(epoch)).limit(1)
            )
        ).first() is not None

    async def _aggregate(self, epoch: int) -> list[MinerEpochStat]:
        """Build (unpersisted) per-(hotkey, model, version) aggregates for ``epoch``.

        Aggregated in Python rather than SQL because the reward-bearing sums are
        conditional on ``verified`` and the percentiles must match
        :func:`percentile_ms` exactly on both SQLite and PostgreSQL — a portable
        GROUP BY would give neither.
        """
        rows = list(
            (
                await self.session.execute(
                    select(MinerRequestStat).where(MinerRequestStat.epoch == int(epoch))
                )
            ).scalars()
        )

        buckets: dict[tuple[str, str, str], dict] = {}
        for row in rows:
            key = (row.miner_hotkey, row.model_id, row.model_version or "")
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = {
                    "subnet_node_id": row.subnet_node_id,
                    "requests_ok": 0,
                    "requests_failed": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "verified_prompt_tokens": 0,
                    "verified_completion_tokens": 0,
                    "verified_total_tokens": 0,
                    "latencies": [],
                }
            if bucket["subnet_node_id"] is None and row.subnet_node_id is not None:
                bucket["subnet_node_id"] = row.subnet_node_id

            served = row.outcome == OUTCOME_SUCCESS
            if served:
                bucket["requests_ok"] += 1
                bucket["prompt_tokens"] += int(row.prompt_tokens or 0)
                bucket["completion_tokens"] += int(row.completion_tokens or 0)
                bucket["total_tokens"] += int(row.total_tokens or 0)
                # Reward-bearing counts require enclave corroboration.
                if row.verified:
                    bucket["verified_prompt_tokens"] += int(row.prompt_tokens or 0)
                    bucket["verified_completion_tokens"] += int(row.completion_tokens or 0)
                    bucket["verified_total_tokens"] += int(row.total_tokens or 0)
            else:
                bucket["requests_failed"] += 1

            # Latency is only meaningful for requests that were actually served; a
            # connect-refused "0 ms" would flatter an unreachable miner.
            if served and row.latency_ms is not None:
                bucket["latencies"].append(int(row.latency_ms))

        anchors = await self._usage_anchors(sorted({key[0] for key in buckets}))

        aggregates: list[MinerEpochStat] = []
        for (hotkey, model_id, model_version), bucket in sorted(buckets.items()):
            anchor = anchors.get((hotkey, model_id), (None, None))
            aggregates.append(
                MinerEpochStat(
                    epoch=int(epoch),
                    miner_hotkey=hotkey,
                    subnet_node_id=bucket["subnet_node_id"],
                    model_id=model_id,
                    model_version=model_version,
                    requests_ok=bucket["requests_ok"],
                    requests_failed=bucket["requests_failed"],
                    prompt_tokens=bucket["prompt_tokens"],
                    completion_tokens=bucket["completion_tokens"],
                    total_tokens=bucket["total_tokens"],
                    verified_prompt_tokens=bucket["verified_prompt_tokens"],
                    verified_completion_tokens=bucket["verified_completion_tokens"],
                    verified_total_tokens=bucket["verified_total_tokens"],
                    latency_p50_ms=percentile_ms(bucket["latencies"], 50),
                    latency_p95_ms=percentile_ms(bucket["latencies"], 95),
                    tee_usage_count_end=anchor[0],
                    tee_usage_chain_head_end=anchor[1],
                )
            )
        return aggregates

    async def _usage_anchors(
        self, hotkeys: Sequence[str]
    ) -> dict[tuple[str, str], tuple[int | None, str | None]]:
        """Current enclave usage anchor per (hotkey, model_id).

        Written by the receipts collector after a verified head signature, so
        publishing it lets a validator cross-check the snapshot against the
        cumulative counters its own attestation probe independently observed.
        """
        if not hotkeys:
            return {}
        # MinerModel is imported lazily-by-attribute through the Miner relationship in
        # the canonical app; here the mirror has no relationship, so join explicitly.
        from inference_api.models import MinerModel

        result = await self.session.execute(
            select(Miner.hotkey, MinerModel.model_id, MinerModel.usage_count, MinerModel.usage_chain_head)
            .join(MinerModel, MinerModel.miner_id == Miner.id)
            .where(Miner.hotkey.in_(list(hotkeys)))
        )
        return {
            (hotkey, model_id): (usage_count, usage_chain_head)
            for hotkey, model_id, usage_count, usage_chain_head in result.all()
        }

    async def _persist_aggregates(self, epoch: int, aggregates: Sequence[MinerEpochStat]) -> None:
        """Store the aggregates behind the artifact so it stays auditable later."""
        for row in aggregates:
            self.session.add(row)
        if aggregates:
            # Flush now so a unique-constraint violation (i.e. a concurrent finalize
            # of the same epoch) surfaces here rather than at an unrelated commit.
            await self.session.flush()


__all__ = ["MinerEpochStatsService", "percentile_ms"]
