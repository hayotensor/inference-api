"""Provisioner tests against the REAL in-process dev TEE producer.

These genuinely exercise the shared path: AsyncTeeClient.get_attestation ->
talaris_attest.verify_attestation -> talaris_contracts.build_provision_request ->
the producer's POST /provision (which verifies the platform signature, unseals,
and stores the creds). The dev TEE returns model_hash/gpu_hash=None (engine not
ready) — handled by the verifier's informational checks.
"""

import uuid

import httpx
from nacl.signing import SigningKey
from sqlalchemy import select

from talaris_contracts import AsyncTeeClient

from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel, ProvisionedToken
from inference_api.provisioner.service import ProvisionerService
from inference_api.security import utcnow

from _tee import bind_dev_tee, dev_tee_app


async def _make_miner(
    hotkey: str = "11" * 32, model_ids: list[str] | None = None
) -> uuid.UUID:
    # The provisioner is now per-(miner, model): a miner with no advertised
    # MinerModel rows has nothing to provision, so seed at least one enclave row.
    model_ids = model_ids if model_ids is not None else ["demo-chat-001"]
    async with async_session_maker() as session:
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=hotkey,
            tee_endpoint="http://tee.local",
            attestation_status="pending",
            health="unknown",
            last_seen=utcnow(),
            registered_at=utcnow(),
        )
        session.add(miner)
        for model_id in model_ids:
            session.add(
                MinerModel(
                    id=uuid.uuid4(),
                    miner_id=miner.id,
                    model_id=model_id,
                    loaded=True,
                    last_advertised_at=utcnow(),
                )
            )
        await session.commit()
        return miner.id


async def test_provision_miner_against_real_dev_tee():
    # The enclave's bound hotkey must equal the registered miner's hotkey so the
    # verifier's expected_miner_hash check (doc.miner == sha256(hotkey)) matches.
    hotkey_signing_key = SigningKey.generate()

    async with dev_tee_app() as (transport, base_url):
        hotkey = await bind_dev_tee(transport, base_url, hotkey_signing_key)
        miner_id = await _make_miner(hotkey=hotkey)

        def tee_client_factory(endpoint):
            return AsyncTeeClient(
                client=httpx.AsyncClient(base_url=base_url, transport=transport)
            )

        async with async_session_maker() as session:
            service = ProvisionerService(session, tee_client_factory=tee_client_factory)
            outcome = await service.provision_miner(miner_id)
            await session.commit()

    assert outcome.attestation_mode == "dev"
    assert outcome.key_id == "platform-default"

    async with async_session_maker() as session:
        miner = await session.get(Miner, miner_id)
        assert miner.attestation_status == "attested", "real /provision must be accepted"
        assert miner.attestation_verified_at is not None
        assert miner.attestation_expiry is not None
        # The enclave's published verify key was recorded.
        assert miner.enclave_verify_key is not None

        # The model-enclave row carries its own per-model attestation state.
        mm = (
            await session.execute(
                select(MinerModel).where(MinerModel.miner_id == miner_id)
            )
        ).scalar_one()
        assert mm.attestation_status == "attested"
        assert mm.attestation_mode == "dev"
        assert mm.enclave_verify_key is not None

        token = (
            await session.execute(
                select(ProvisionedToken).where(
                    ProvisionedToken.miner_id == miner_id,
                    ProvisionedToken.status == "active",
                )
            )
        ).scalar_one()
        # The credential is bound to the specific model-enclave.
        assert token.model_id == mm.model_id
        assert token.encrypted_token  # stored encrypted (Fernet ciphertext)
        assert token.admin_encrypted_token


async def test_provision_marks_miner_failed_on_unreachable_tee():
    """A provision that cannot reach the enclave marks the miner 'failed'."""
    miner_id = await _make_miner(hotkey="22" * 32)

    def broken_factory(endpoint):
        # Point at a transport with no server -> get_attestation raises.
        return AsyncTeeClient(base_url="http://127.0.0.1:1/")

    from inference_api.provisioner.service import ProvisionError

    async with async_session_maker() as session:
        service = ProvisionerService(session, tee_client_factory=broken_factory)
        try:
            await service.provision_miner(miner_id)
            raised = False
        except ProvisionError:
            raised = True
        await session.commit()
    assert raised

    async with async_session_maker() as session:
        miner = await session.get(Miner, miner_id)
        assert miner.attestation_status == "failed"


async def test_provision_rejects_miner_hash_mismatch():
    """A miner whose hotkey != the enclave's bound key fails the miner_hash check.

    The dev enclave is bound to key A, but the registered miner's hotkey is a
    DIFFERENT key B. The verifier's expected_miner_hash check (doc.miner ==
    sha256(hotkey_B)) fails, so provisioning is rejected and the miner is marked
    failed — proving the registration<->attestation binding is enforced via the
    shared verifier (not reimplemented here).
    """
    enclave_key = SigningKey.generate()
    other_key = SigningKey.generate()
    other_hotkey = bytes(other_key.verify_key).hex()

    from inference_api.provisioner.service import ProvisionError

    async with dev_tee_app() as (transport, base_url):
        await bind_dev_tee(transport, base_url, enclave_key)  # enclave bound to A
        miner_id = await _make_miner(hotkey=other_hotkey)  # registered as B

        def factory(_endpoint):
            return AsyncTeeClient(
                client=httpx.AsyncClient(base_url=base_url, transport=transport)
            )

        async with async_session_maker() as session:
            service = ProvisionerService(session, tee_client_factory=factory)
            try:
                await service.provision_miner(miner_id)
                raised = False
            except ProvisionError:
                raised = True
            await session.commit()
    assert raised

    async with async_session_maker() as session:
        miner = await session.get(Miner, miner_id)
        assert miner.attestation_status == "failed"


async def test_provision_multi_model_miner_attests_each_enclave():
    """A miner advertising N models gets EACH enclave attested + its own token.

    Both model-enclaves resolve to the same in-process dev TEE here (the factory
    ignores the endpoint), but the provisioner runs a fresh per-model attest +
    seal for each, stamps per-model attestation state, and mints a per-(miner,
    model) active ProvisionedToken (model_id set) so the per-model selector join
    resolves each enclave independently.
    """
    hotkey_signing_key = SigningKey.generate()
    model_ids = ["demo-chat-001", "demo-chat-002"]

    async with dev_tee_app() as (transport, base_url):
        hotkey = await bind_dev_tee(transport, base_url, hotkey_signing_key)
        miner_id = await _make_miner(hotkey=hotkey, model_ids=model_ids)

        def factory(_endpoint):
            return AsyncTeeClient(
                client=httpx.AsyncClient(base_url=base_url, transport=transport)
            )

        async with async_session_maker() as session:
            service = ProvisionerService(session, tee_client_factory=factory)
            await service.provision_miner(miner_id)
            await session.commit()

    async with async_session_maker() as session:
        miner = await session.get(Miner, miner_id)
        assert miner.attestation_status == "attested"

        models = (
            await session.execute(
                select(MinerModel).where(MinerModel.miner_id == miner_id)
            )
        ).scalars().all()
        assert {m.model_id for m in models} == set(model_ids)
        # EACH model-enclave is independently attested.
        assert all(m.attestation_status == "attested" for m in models)
        assert all(m.enclave_verify_key is not None for m in models)

        # EACH (miner, model) has exactly one active token bound to its model_id.
        for model_id in model_ids:
            token = (
                await session.execute(
                    select(ProvisionedToken).where(
                        ProvisionedToken.miner_id == miner_id,
                        ProvisionedToken.model_id == model_id,
                        ProvisionedToken.status == "active",
                    )
                )
            ).scalar_one()
            assert token.encrypted_token
            assert token.admin_encrypted_token
