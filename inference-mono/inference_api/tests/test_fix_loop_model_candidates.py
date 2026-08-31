"""The provisioner loop must notice an un-attested MODEL on an attested miner.

Regression test for a latent bug found while verifying the other fixes: adding or
re-registering a model on an already-attested miner creates the `MinerModel` row as
"pending" (``MinerRegistryService``) but only resets the MINER's status when the TEE
endpoint or key changed. Because ``_candidates()`` selected purely on
``Miner.attestation_status``, that model was never provisioned, and ``MinerSelector``
(which requires an attested ``MinerModel``) would never route to it — a silent,
permanent 503 for that model on an otherwise healthy, attested miner.

This was previously masked: before the fail-closed fix, such a request quietly fell
through to the fabricated echo response instead of surfacing the problem.
"""

import uuid

from sqlalchemy import update

from inference_api.db import async_session_maker
from inference_api.models import MinerModel
from inference_api.provisioner.loop import _candidates


async def _set_model_status(miner_id: uuid.UUID, status: str) -> None:
    async with async_session_maker() as session:
        await session.execute(
            update(MinerModel).where(MinerModel.miner_id == miner_id).values(attestation_status=status)
        )
        await session.commit()


async def test_attested_miner_with_pending_model_is_a_candidate(seed_miner):
    """The exact stuck state: miner attested (no expiry), its model still pending."""
    seeded = await seed_miner(model_ids=["stuck-model"], attestation_status="attested")
    await _set_model_status(seeded.miner_id, "pending")

    assert seeded.miner_id in await _candidates()


async def test_attested_miner_with_stale_model_is_a_candidate(seed_miner):
    seeded = await seed_miner(model_ids=["stale-model"], attestation_status="attested")
    await _set_model_status(seeded.miner_id, "stale")

    assert seeded.miner_id in await _candidates()


async def test_fully_attested_miner_is_not_reprovisioned(seed_miner):
    """Guard against the fix over-selecting: a healthy attested pair must be left alone."""
    seeded = await seed_miner(model_ids=["settled-model"], attestation_status="attested")

    assert seeded.miner_id not in await _candidates()


async def test_pending_miner_is_still_a_candidate(seed_miner):
    """The original selection behavior must be preserved."""
    seeded = await seed_miner(model_ids=["new-model"], attestation_status="pending")

    assert seeded.miner_id in await _candidates()
