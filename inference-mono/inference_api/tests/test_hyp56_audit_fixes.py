"""HYP-56 internal audit (part 1) regression tests for the serving plane."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from inference_api import product_routes
from inference_api.config import Settings
from inference_api.db import async_session_maker
from inference_api.models import InferenceUsageEvent

from _tee import StubChatTransport, dev_tee_app
from test_fix_a_platform_fixes import _provision_real_miner


def _production(**overrides) -> Settings:
    values = dict(
        app_env="production",
        secret_pepper="p" * 40,
        allowed_hosts=["serve.example.com"],
        rate_limit_fail_open=False,
        # The test env (conftest) turns the dev switches on; a real production
        # env must not, so the baseline pins them to their production values.
        allow_dev_attestation=False,
        allow_dev_echo_fallback=False,
        verifier_backend="auto",
        tls_pin_enforce=True,
        debug=False,
    )
    values.update(overrides)
    return Settings(**values)


def test_production_baseline_settings_are_accepted():
    assert _production().app_env == "production"


def test_production_refuses_allow_dev_attestation():
    with pytest.raises(ValidationError, match="ALLOW_DEV_ATTESTATION"):
        _production(allow_dev_attestation=True)


@pytest.mark.parametrize("backend", ["stub", "mock", "dev"])
def test_production_refuses_dev_verifier_backends(backend):
    with pytest.raises(ValidationError, match="VERIFIER_BACKEND"):
        _production(verifier_backend=backend)


def test_production_refuses_disabled_tls_pinning():
    with pytest.raises(ValidationError, match="TLS_PIN_ENFORCE"):
        _production(tls_pin_enforce=False)


def test_dev_switches_still_allowed_outside_production():
    s = Settings(app_env="local", allow_dev_attestation=True, verifier_backend="stub", tls_pin_enforce=False)
    assert s.allow_dev_attestation and s.verifier_backend == "stub"


# --------------------------------------------------------------------------- #
# Streaming: a client that hangs up before the trailing usage chunk must still
# be billed for the text it received (it used to be released -> free).
# --------------------------------------------------------------------------- #

class _NoUsageStreamTransport(StubChatTransport):
    """Streams generated text but never the trailing usage chunk -- exactly
    what the platform sees when the client disconnects at ``finish_reason``."""

    def _stream_response(self, body: dict) -> httpx.Response:
        body = dict(body)
        body["stream_options"] = {"include_usage": False}
        return super()._stream_response(body)


async def test_stream_without_usage_chunk_bills_relayed_text_not_free(client, seed_api_key):
    seeded = await seed_api_key(credits=10_000)
    try:
        async with dev_tee_app() as (transport, base_url):
            await _provision_real_miner(transport, base_url)
            product_routes.set_forwarder_transport(_NoUsageStreamTransport(transport))
            resp = await client.post(
                "/v1/chat/completions",
                headers={"X-API-Key": seeded.raw_key},
                json={
                    "model": "demo-chat-001",
                    "messages": [{"role": "user", "content": "stream then hang up"}],
                    "stream": True,
                    "max_tokens": 64,
                },
            )
            assert resp.status_code == 200, resp.text
            assert "Hello from the stubbed enclave engine." in resp.text
    finally:
        product_routes.set_forwarder_transport(None)

    async with async_session_maker() as session:
        events = (
            await session.execute(
                select(InferenceUsageEvent).where(InferenceUsageEvent.api_key_id == seeded.api_key_id)
            )
        ).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.status == "settled"
    assert event.completion_tokens == 1  # one content-bearing chunk relayed
    assert event.charged_tokens > 0


# --------------------------------------------------------------------------- #
# P2-16 (handed to Part 1): an UNBOUND enclave must not be provisioned outside
# the dev attestation gate -- nothing ties it to the registered miner.
# --------------------------------------------------------------------------- #


async def _unbound_miner(base_url: str):
    import uuid as _uuid

    from inference_api.models import Miner, MinerModel
    from inference_api.security import utcnow

    async with async_session_maker() as session:
        miner = Miner(
            id=_uuid.uuid4(),
            hotkey="22" * 32,
            tee_endpoint=base_url,
            attestation_status="pending",
            health="healthy",
            last_seen=utcnow(),
            registered_at=utcnow(),
        )
        session.add(miner)
        session.add(
            MinerModel(
                id=_uuid.uuid4(), miner_id=miner.id, model_id="demo-chat-001",
                loaded=True, last_advertised_at=utcnow(),
            )
        )
        await session.commit()
        return miner.id


@pytest.mark.parametrize("dev_gate", [False, True])
async def test_unbound_enclave_is_refused_outside_the_dev_gate(monkeypatch, dev_gate):
    from talaris_contracts import AsyncTeeClient

    from inference_api.config import settings
    from inference_api.provisioner.service import ProvisionError, ProvisionerService

    async with dev_tee_app() as (transport, base_url):  # never bound -> doc.miner is None
        miner_id = await _unbound_miner(base_url)

        def factory(_endpoint):
            return AsyncTeeClient(client=httpx.AsyncClient(base_url=base_url, transport=transport))

        monkeypatch.setattr(settings, "allow_dev_attestation", dev_gate)
        async with async_session_maker() as session:
            service = ProvisionerService(session, tee_client_factory=factory)
            try:
                await service.provision_miner(miner_id)
                error = None
            except ProvisionError as exc:
                error = str(exc)
    if dev_gate:
        assert error is None or "not bound to a miner" not in error
    else:
        assert error is not None and "not bound to a miner" in error
