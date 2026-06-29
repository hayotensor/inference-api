"""GET /allowlist serves a public, platform-signed ModelAllowlistArtifact."""

import uuid

from talaris_contracts import ModelAllowlistArtifact, verify_model_allowlist

from inference_api.allowlist_seed import DEFAULT_MODEL_ALLOWLIST
from inference_api.db import async_session_maker
from inference_api.models import ModelAllowlist
from inference_api.provisioner.key import load_provisioner_signing_key
from inference_api.security import utcnow


def _platform_pubkey_hex() -> str:
    return load_provisioner_signing_key().verify_key.encode().hex()


async def test_allowlist_is_public_and_signed(client):
    # No auth header: the artifact is signed, so the read is public.
    resp = await client.get("/allowlist")
    assert resp.status_code == 200, resp.text

    artifact = ModelAllowlistArtifact.model_validate(resp.json())
    # Verifies against the PINNED platform verify key (same key as provisioning).
    assert verify_model_allowlist(artifact, _platform_pubkey_hex()) is True
    # not_before is bound to the version (the determinism contract).
    assert artifact.not_before == artifact.version


async def test_allowlist_reflects_seeded_rows(client):
    resp = await client.get("/allowlist")
    assert resp.status_code == 200, resp.text
    artifact = ModelAllowlistArtifact.model_validate(resp.json())

    served = {(e.model_id, e.model_version, e.model_hash) for e in artifact.entries}
    for seed in DEFAULT_MODEL_ALLOWLIST:
        assert (seed.model_id, seed.model_version, seed.model_hash) in served
    # Seeding actually populated the table.
    assert len(artifact.entries) >= len(DEFAULT_MODEL_ALLOWLIST)


async def test_allowlist_excludes_inactive_rows(client):
    async with async_session_maker() as session:
        session.add(
            ModelAllowlist(
                id=uuid.uuid4(),
                model_id="retired-model",
                model_version="v1",
                model_hash="f" * 64,
                active=False,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        await session.commit()

    resp = await client.get("/allowlist")
    artifact = ModelAllowlistArtifact.model_validate(resp.json())
    assert "retired-model" not in {e.model_id for e in artifact.entries}


async def test_allowlist_version_is_monotonic(client):
    first = ModelAllowlistArtifact.model_validate((await client.get("/allowlist")).json())

    # A second read of an unchanged table signs the identical (stable) version.
    second = ModelAllowlistArtifact.model_validate((await client.get("/allowlist")).json())
    assert second.version >= first.version
    assert second.signature == first.signature

    # Adding an active row advances the table state -> non-decreasing version.
    async with async_session_maker() as session:
        session.add(
            ModelAllowlist(
                id=uuid.uuid4(),
                model_id="newly-approved",
                model_version="v2",
                model_hash="a" * 64,
                active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        await session.commit()

    third = ModelAllowlistArtifact.model_validate((await client.get("/allowlist")).json())
    assert third.version >= second.version
    assert "newly-approved" in {e.model_id for e in third.entries}
    assert verify_model_allowlist(third, _platform_pubkey_hex()) is True
