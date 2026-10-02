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


# Label stamped on a row this platform populated itself (as opposed to an
# operator-curated pre-approval) from a real, hardware-verified attestation.
# Kept distinct from the "demo" label so a consumer can always tell "someone
# vouched for this in advance" apart from "the platform observed this the
# first time a miner attested it" -- see sync_observed_model_allowlist.
OBSERVED_ATTESTED_LABEL = "observed-attested"

# A model_hash of all zeros is never a genuine hardware-measured hash (see
# DEFAULT_MODEL_ALLOWLIST's demo entry above) -- refuse to publish it as if it
# were an observed, cryptographically real value.
_PLACEHOLDER_MODEL_HASH = "0" * 64


async def sync_observed_model_allowlist(
    session: AsyncSession,
    *,
    model_id: str,
    model_version: str | None,
    model_hash: str | None,
) -> None:
    """Trust-on-first-attest: publish a genuinely observed attested model_hash.

    Called by the provisioner immediately after ``talaris_attest.
    verify_attestation`` has independently verified a miner's hardware
    attestation (see ``inference_api/provisioner/service.py``) -- so
    ``model_hash`` here is never a self-reported value, it is what the
    verifier itself extracted from the checked TEE evidence.

    This ONLY adds a NEW active row when no active allowlist entry exists yet
    for this ``(model_id, model_version, model_hash)`` triple; it never
    overwrites or deactivates an existing (e.g. operator pre-approved) row, and
    an operator who later curates their own row for the same model is free to
    deactivate this one. The purpose is narrow: today an unpinned model_id has
    NO allowlist row at all, which makes ``ProvisionerService._model_allowlist``
    skip the model-hash check entirely (see its docstring) -- pinning the
    first hash this platform ever independently verified for a model is a
    strict improvement over that silent no-check default, and it is what lets
    ``GET /allowlist`` ever show a real (non-"demo") model_hash without an
    operator having to hand-populate the table first.

    A blank/placeholder (all-zero, e.g. an unstarted dev enclave's
    ``model_hash=None``) is never published -- there is nothing genuine to pin.
    """
    if not model_hash or model_hash.lower() == _PLACEHOLDER_MODEL_HASH:
        return
    result = await session.execute(
        select(ModelAllowlist).where(
            ModelAllowlist.model_id == model_id,
            ModelAllowlist.active.is_(True),
        )
    )
    if result.first() is not None:
        # An active pin (operator-curated or previously observed) already
        # exists for this model_id -- do not silently add another trust root.
        return
    session.add(
        ModelAllowlist(
            model_id=model_id,
            model_version=model_version,
            model_hash=model_hash,
            label=OBSERVED_ATTESTED_LABEL,
            active=True,
        )
    )
    await session.flush()


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
