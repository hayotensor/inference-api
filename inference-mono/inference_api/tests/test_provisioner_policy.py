"""Policy-construction tests for the provisioner's attestation verification.

These pin the wiring between inference-api settings and the SHARED policy factory
``talaris_attest.build_expected_claims`` (re-exported through the provisioner as
``service.build_expected_claims``). They assert two things WITHOUT running a full
provision or touching the network:

  1. With ``allow_dev_attestation=False`` + NRAS config set, the provisioner builds
     a PRODUCTION ``ExpectedClaims`` whose backends are the real backend types and
     whose ``allow_insecure_dev is False`` (the dev gate is shut).
  2. The in-process dev TEE document, which the dev policy accepts, is REJECTED
     (fails closed) under that production policy — flipping ``allow_dev_attestation``
     to False makes the dev TEE no longer accepted.

The provisioner must reuse the factory; we deliberately assert on the REAL backend
types (``NvidiaNrasBackend`` / ``IntelPcsTdxBackend``) the factory constructs.
"""

import secrets

import httpx
import pytest

from talaris_attest import verify_attestation
from talaris_attest.backends.intel_pcs import IntelPcsTdxBackend
from talaris_attest.backends.nvidia_nras import NvidiaNrasBackend
from talaris_contracts import AsyncTeeClient, miner_hash as contracts_miner_hash

from inference_api.provisioner import service as provisioner_service

from _tee import dev_tee_app


_FAKE_NVIDIA_ROOT_PEM = (
    "-----BEGIN CERTIFICATE-----\nZmFrZQ==\n-----END CERTIFICATE-----\n"
)


@pytest.fixture
def production_attestation_settings(monkeypatch):
    """Flip the live provisioner settings onto the production attestation path.

    ``service.py`` binds the module-level ``settings`` object, so patching its
    attributes is sufficient (and is what the production process would see from
    env). NRAS config is supplied so the NVIDIA backend is fully wired; the
    bundled-real Intel defaults need no config.
    """
    s = provisioner_service.settings
    monkeypatch.setattr(s, "allow_dev_attestation", False, raising=False)
    monkeypatch.setattr(s, "verifier_backend", "auto", raising=False)
    monkeypatch.setattr(s, "nras_jwks_url", "https://nras.example/jwks", raising=False)
    monkeypatch.setattr(
        s, "nras_expected_issuer", "https://nras.example", raising=False
    )
    monkeypatch.setattr(s, "nras_url", "https://nras.example/v3/attest/gpu", raising=False)
    monkeypatch.setattr(s, "nvidia_root_pem", _FAKE_NVIDIA_ROOT_PEM, raising=False)
    monkeypatch.setattr(s, "intel_require_collateral", True, raising=False)
    monkeypatch.setattr(s, "intel_allowed_tcb_statuses", None, raising=False)
    monkeypatch.setattr(s, "require_tdx_quote", True, raising=False)
    monkeypatch.setattr(s, "require_gpu_evidence", True, raising=False)
    return s


def test_production_policy_uses_real_backends(production_attestation_settings):
    """allow_dev_attestation=False + NRAS config -> real backends, dev gate shut."""
    expected = provisioner_service.build_expected_claims(
        expected_miner_hash="ab" * 32
    )

    # The dev gate is shut and only hardware-attested ("nvidia") docs pass.
    assert expected.allow_insecure_dev is False
    assert expected.required_modes == frozenset({"nvidia"})

    # The factory wired the REAL backend types (not stubs, not hand-rolled).
    assert isinstance(expected.nvidia_backend, NvidiaNrasBackend)
    assert isinstance(expected.tdx_backend, IntelPcsTdxBackend)

    # Hardware-evidence requirements flow through from settings.
    assert expected.require_gpu_evidence is True
    assert expected.require_tdx_quote is True


def test_dev_policy_selects_stub_path_by_default():
    """The default test/dev config (ALLOW_DEV_ATTESTATION=true) stays insecure-dev."""
    # conftest sets ALLOW_DEV_ATTESTATION=true, so the live settings are dev.
    assert provisioner_service.settings.allow_dev_attestation is True
    expected = provisioner_service.build_expected_claims(expected_miner_hash="cd" * 32)
    assert expected.allow_insecure_dev is True
    assert expected.required_modes == frozenset({"dev"})
    # Stub backends are auto-selected by verify_attestation (left None here).
    assert expected.nvidia_backend is None
    assert expected.tdx_backend is None


async def test_dev_tee_doc_rejected_under_production_policy(
    production_attestation_settings,
):
    """The in-process dev TEE doc the dev policy accepts is REJECTED in production.

    We fetch a real dev-TEE attestation document, then verify it under BOTH the
    dev policy (accepts) and the production policy the provisioner now builds
    (must fail closed: dev mode is rejected). No network is needed — verification
    fails at the mode/dev gate before any backend call.
    """
    nonce = secrets.token_bytes(32)
    expected_hash = contracts_miner_hash("11" * 32)

    async with dev_tee_app() as (transport, base_url):
        client = AsyncTeeClient(
            client=httpx.AsyncClient(base_url=base_url, transport=transport)
        )
        try:
            doc = await client.get_attestation(nonce)
        finally:
            await client.aclose()

    # Sanity: the document is a dev-mode document.
    assert doc.mode == "dev"

    # 1. Under the production policy the provisioner builds, the dev doc FAILS.
    prod_policy = provisioner_service.build_expected_claims(
        expected_miner_hash=expected_hash
    )
    assert prod_policy.allow_insecure_dev is False
    prod_result = verify_attestation(doc, prod_policy, nonce)
    assert prod_result.ok is False, "dev TEE doc must be rejected under production policy"

    # 2. The same doc verifies under a dev policy (allow_insecure_dev=True),
    #    proving the rejection above is the dev gate closing — not an unrelated
    #    failure. We build the dev policy through the factory too.
    dev_policy = provisioner_service.build_expected_claims_factory(
        allow_dev=True, expected_miner_hash=doc.miner
    )
    assert dev_policy.allow_insecure_dev is True
    dev_result = verify_attestation(doc, dev_policy, nonce)
    assert dev_result.ok is True, "dev TEE doc must verify under the dev policy"
