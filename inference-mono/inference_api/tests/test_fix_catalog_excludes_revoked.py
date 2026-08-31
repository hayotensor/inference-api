"""A revoked miner's models must leave the catalog, not linger as "degraded".

`degraded` means "registered but temporarily unservable" — a customer is meant to
understand that it may come back. A revoked miner is gone for good, so advertising
its models invites picking something that can never be served: exactly what happened
in testing, where a stale revoked registration stayed in the chat model picker and
every selection failed.

Such a model must therefore read as never-registered (404 model_not_found) rather
than as temporarily out of capacity (503 no_capacity).
"""

import uuid

from sqlalchemy import update

from inference_api.db import async_session_maker
from inference_api.models import Miner
from inference_api.product_routes import _all_registered_model_ids, _is_model_registered


async def _revoke(miner_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        await session.execute(
            update(Miner).where(Miner.id == miner_id).values(attestation_status="revoked")
        )
        await session.commit()


async def test_revoked_miner_model_leaves_the_catalog(seed_miner):
    seeded = await seed_miner(model_ids=["doomed-model"], attestation_status="attested")

    async with async_session_maker() as session:
        assert "doomed-model" in await _all_registered_model_ids(session)

    await _revoke(seeded.miner_id)

    async with async_session_maker() as session:
        assert "doomed-model" not in await _all_registered_model_ids(session)


async def test_revoked_miner_model_reads_as_never_registered(seed_miner):
    """Drives the 404-vs-503 split: revoked must be 404, not 503."""
    seeded = await seed_miner(model_ids=["doomed-too"], attestation_status="attested")
    await _revoke(seeded.miner_id)

    async with async_session_maker() as session:
        assert await _is_model_registered(session, "doomed-too") is False


async def test_unhealthy_but_live_miner_stays_listed(seed_miner):
    """Guard against over-filtering: a retryable state must remain visible."""
    seeded = await seed_miner(model_ids=["still-hoping"], attestation_status="pending")

    async with async_session_maker() as session:
        assert "still-hoping" in await _all_registered_model_ids(session)
        assert await _is_model_registered(session, "still-hoping") is True


async def test_model_served_by_two_miners_survives_one_revocation(seed_miner):
    """One dead host must not delist a model another miner still advertises."""
    dead = await seed_miner(model_ids=["shared-model"], attestation_status="attested")
    await seed_miner(model_ids=["shared-model"], attestation_status="attested")
    await _revoke(dead.miner_id)

    async with async_session_maker() as session:
        assert "shared-model" in await _all_registered_model_ids(session)
