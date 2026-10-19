"""talaris-e2ee/v1 relay + billing (``/v1/e2e/*``).

The enclave side is a minimal FAKE TEE (``FakeEnclave``) built only from
``talaris_contracts.e2ee`` -- real HPKE, real nonce burning, real sealing -- so
these tests exercise the actual wire format end to end without depending on
tee_wrapper's in-progress E2EE router. The client side of every roundtrip uses
``seal_request`` / ``open_response`` / ``StreamOpener``, i.e. it proves the
relay passed the ciphertext through untouched in both directions.

A separate test at the bottom runs the same roundtrip against the REAL
tee_wrapper (skipped if its E2EE router is not present).
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select

from talaris_contracts.e2ee import (
    H_ENCRYPTED,
    H_ERROR,
    H_INSTANCE,
    H_RETRY,
    H_USAGE,
    E2EEError,
    E2EEKeyPair,
    RequestMeta,
    StreamOpener,
    StreamSealer,
    format_usage,
    key_id,
    open_request,
    open_response,
    seal_request,
    seal_response,
    sse_event,
)

from inference_api import product_routes
from inference_api.config import settings
from inference_api.crypto import encrypt_token
from inference_api.db import async_session_maker
from inference_api.models import (
    InferenceUsageEvent,
    Miner,
    MinerModel,
    MinerRequestStat,
    ProvisionedToken,
    User,
)
from inference_api.security import utcnow
from inference_api.usage import UsageService, charged_token_count

MODEL = "medium-model"  # default pricing multiplier 2 -> proves the multiplier is applied
INFERENCE_TOKEN = "inf_e2ee_test_token"


# --------------------------------------------------------------------------- #
# Fake enclave
# --------------------------------------------------------------------------- #


class FakeEnclave:
    """One E2EE enclave: /attestation, /e2e/nonces, /e2e/invoke over talaris_contracts."""

    def __init__(self, *, prompt_tokens=13, completion_tokens=7, chunks=("Hel", "lo", "!")):
        self.keypair = E2EEKeyPair.generate()
        self.public_hex = self.keypair.public_bytes.hex()
        self.served_key_hex = self.public_hex  # override to simulate a swapped key
        self.nonces: set[str] = set()
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.chunks = chunks
        self.include_usage = True
        self.engine_status = 200
        self.invocations = 0
        self.seen_headers: dict[str, str] = {}
        self.app = self._build()

    def usage(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }

    def _build(self) -> FastAPI:
        app = FastAPI()
        enclave = self

        def authed(request: Request) -> bool:
            return request.headers.get("authorization") == f"Bearer {INFERENCE_TOKEN}"

        @app.get("/attestation")
        async def attestation(nonce: str):
            return {"nonce": nonce, "mode": "dev", "public_bundle": {"e2ee_key": enclave.served_key_hex}}

        @app.post("/e2e/nonces")
        async def nonces(request: Request):
            if not authed(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            count = (await request.json())["count"]
            issued = [secrets.token_hex(32) for _ in range(count)]
            enclave.nonces.update(issued)
            return {
                "nonces": issued,
                "expires_in": 60,
                "e2ee_key": enclave.served_key_hex,
                "key_id": key_id(bytes.fromhex(enclave.served_key_hex)),
            }

        @app.post("/e2e/invoke")
        async def invoke(request: Request):
            if not authed(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            enclave.invocations += 1
            enclave.seen_headers = dict(request.headers)
            try:
                meta = RequestMeta.from_headers(request.headers)
                if meta.nonce not in enclave.nonces:
                    raise E2EEError("nonce_unknown", "nonce is unknown, expired or already used")
                enclave.nonces.discard(meta.nonce)
                body, response_key = open_request(enclave.keypair, meta, await request.body())
            except E2EEError as err:
                return JSONResponse(err.to_body(), status_code=err.status, headers=err.to_headers())
            text = "".join(enclave.chunks)
            if not meta.stream:
                engine_body = json.dumps(
                    {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": enclave.usage(),
                     "echo_messages": body.get("messages")}
                ).encode()
                headers = {H_ENCRYPTED: "1"}
                if enclave.include_usage:
                    headers[H_USAGE] = format_usage(**enclave.usage())
                return Response(
                    seal_response(response_key, meta, enclave.engine_status, engine_body),
                    status_code=enclave.engine_status,
                    media_type="application/octet-stream",
                    headers=headers,
                )
            sealer = StreamSealer(response_key, meta)

            async def events():
                for piece in enclave.chunks:
                    chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
                    yield sse_event(sealer.chunk(json.dumps(chunk)))
                if enclave.include_usage:
                    yield sse_event({"usage": enclave.usage()})
                yield sse_event(sealer.final({"usage": enclave.usage()}))
                yield sse_event("[DONE]")

            return StreamingResponse(events(), media_type="text/event-stream", headers={H_ENCRYPTED: "1"})

        return app


class HostRouter(httpx.AsyncBaseTransport):
    """Route each enclave host to its own in-process ASGI app; unknown -> refused."""

    def __init__(self, apps: dict[str, FastAPI]) -> None:
        self._transports = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._transports.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError("connection refused", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture
def reset_forwarder_transport():
    yield
    product_routes.set_forwarder_transport(None)


async def seed_enclave(
    host: str,
    *,
    e2ee_key: str | None,
    model_id: str = MODEL,
    tls_cert_fingerprint: str | None = "ab" * 32,
    health: str = "healthy",
) -> tuple[uuid.UUID, uuid.UUID]:
    """An attested+loaded+healthy+tokened model-enclave. Returns (miner_id, miner_model_id)."""
    async with async_session_maker() as session:
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=secrets.token_hex(32),
            tee_endpoint=f"http://{host}",
            attestation_status="attested",
            attestation_mode="dev",
            health=health,
            tls_cert_fingerprint=tls_cert_fingerprint,
            enclave_verify_key="cd" * 32,
            capacity={"available_concurrent_requests": 4, "queue_depth": 0},
            last_seen=utcnow(),
            registered_at=utcnow(),
        )
        mm = MinerModel(
            id=uuid.uuid4(),
            miner_id=miner.id,
            model_id=model_id,
            model_hash="ef" * 32,
            attestation_status="attested",
            attestation_mode="dev",
            e2ee_public_key=e2ee_key,
            loaded=True,
            last_advertised_at=utcnow(),
        )
        session.add_all([miner, mm])
        session.add(
            ProvisionedToken(
                id=uuid.uuid4(),
                miner_id=miner.id,
                model_id=model_id,
                key_id="platform-default",
                encrypted_token=encrypt_token(INFERENCE_TOKEN),
                status="active",
                provisioned_at=utcnow(),
            )
        )
        await session.commit()
        return miner.id, mm.id


def auth(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}"}


async def list_instances(client, raw_key, *, model=MODEL, **params):
    return await client.get("/v1/e2e/instances", params={"model": model, **params}, headers=auth(raw_key))


async def invoke(client, raw_key, instance, *, stream=False, max_tokens=64, nonce=None, body=None,
                 model=MODEL, request_id=None):
    """Seal a chat request to ``instance`` exactly as a client would; return (resp, meta, response_key)."""
    meta = RequestMeta(nonce=nonce or instance["nonces"][0], path="/v1/chat/completions", model=model,
                       stream=stream, max_tokens=max_tokens)
    blob, response_key = seal_request(
        bytes.fromhex(instance["e2ee_key"]),
        meta,
        body or {"model": model, "stream": stream, "messages": [{"role": "user", "content": "secret prompt"}]},
    )
    headers = {**auth(raw_key), **meta.to_headers(), H_INSTANCE: instance["instance_id"],
               "Content-Type": "application/octet-stream"}
    if request_id:
        headers["X-Request-ID"] = request_id
    resp = await client.post("/v1/e2e/invoke", content=blob, headers=headers)
    return resp, meta, response_key, blob


def open_stream(text: str, response_key: bytes, meta: RequestMeta) -> tuple[str, dict]:
    opener = StreamOpener(response_key, meta)
    out = []
    for line in text.split("\n"):
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payload = opener.open(json.loads(line[len("data: "):]))
        if payload is not None:
            # The engine's own trailing usage chunk (choices == []) is sealed too.
            for choice in json.loads(payload).get("choices") or []:
                out.append(choice["delta"].get("content") or "")
    assert opener.finished
    return "".join(out), opener.trailer


async def remaining_tokens(user_id) -> int:
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        period = await UsageService(session).current_period(user)
        await session.commit()
        return period.remaining_tokens


async def events() -> list[InferenceUsageEvent]:
    async with async_session_maker() as session:
        return list((await session.execute(select(InferenceUsageEvent))).scalars())


# --------------------------------------------------------------------------- #
# GET /v1/e2e/instances
# --------------------------------------------------------------------------- #


async def test_instances_lists_only_e2ee_capable_with_nonces(client, seed_api_key, reset_forwarder_transport):
    seeded = await seed_api_key(credits=100_000)
    good = FakeEnclave()
    _m1, good_id = await seed_enclave("good.tee", e2ee_key=good.public_hex)
    await seed_enclave("legacy.tee", e2ee_key=None)  # pre-E2EE enclave: never offered
    product_routes.set_forwarder_transport(HostRouter({"good.tee": good.app, "legacy.tee": FakeEnclave().app}))

    resp = await list_instances(client, seeded.raw_key, nonces=5)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["object"] == "list" and data["model"] == MODEL
    assert [i["instance_id"] for i in data["instances"]] == [str(good_id)]
    inst = data["instances"][0]
    assert inst["e2ee_key"] == good.public_hex
    assert inst["key_id"] == key_id(good.keypair.public_bytes)
    assert len(inst["nonces"]) == 5 and set(inst["nonces"]) <= good.nonces
    assert inst["nonce_expires_in"] == 60
    assert inst["attestation_mode"] == "dev"
    assert inst["model_hash"] == "ef" * 32


async def test_instances_key_mismatch_is_skipped_and_marked_stale(client, seed_api_key, reset_forwarder_transport):
    seeded = await seed_api_key(credits=100_000)
    good, swapped = FakeEnclave(), FakeEnclave()
    await seed_enclave("good.tee", e2ee_key=good.public_hex)
    bad_miner, _ = await seed_enclave("swapped.tee", e2ee_key=swapped.public_hex)
    swapped.served_key_hex = E2EEKeyPair.generate().public_bytes.hex()  # rebooted / substituted
    # A third enclave that is unreachable: silently skipped, NOT staled.
    dead_miner, _ = await seed_enclave("dead.tee", e2ee_key=FakeEnclave().public_hex)
    product_routes.set_forwarder_transport(HostRouter({"good.tee": good.app, "swapped.tee": swapped.app}))

    resp = await list_instances(client, seeded.raw_key, limit=5)
    assert resp.status_code == 200, resp.text
    assert [i["e2ee_key"] for i in resp.json()["instances"]] == [good.public_hex]
    async with async_session_maker() as session:
        assert (await session.get(Miner, bad_miner)).attestation_status == "stale"
        assert (await session.get(Miner, dead_miner)).attestation_status == "attested"


async def test_instances_404_unknown_model_and_503_no_capacity(client, seed_api_key, reset_forwarder_transport):
    seeded = await seed_api_key(credits=100_000)
    resp = await list_instances(client, seeded.raw_key, model="never-registered")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "model_not_found"

    await seed_enclave("legacy.tee", e2ee_key=None)  # registered, but not E2EE-capable
    resp = await list_instances(client, seeded.raw_key)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "no_capacity"


async def test_instances_param_bounds_and_auth(client, seed_api_key):
    seeded = await seed_api_key(credits=100_000)
    assert (await list_instances(client, seeded.raw_key, limit=6)).status_code == 422
    assert (await list_instances(client, seeded.raw_key, nonces=17)).status_code == 422
    assert (await client.get("/v1/e2e/instances", params={"model": MODEL})).status_code == 401
    readonly = await seed_api_key(raw_key="sk_readonly", scopes=["models:read"])
    assert (await list_instances(client, readonly.raw_key)).status_code == 403


async def test_instances_respects_key_model_allowlist(client, seed_api_key, reset_forwarder_transport):
    seeded = await seed_api_key(credits=100_000, allowed_models=["demo-chat-001"])
    enclave = FakeEnclave()
    await seed_enclave("good.tee", e2ee_key=enclave.public_hex)
    product_routes.set_forwarder_transport(HostRouter({"good.tee": enclave.app}))
    resp = await list_instances(client, seeded.raw_key)
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# GET /v1/e2e/instances/{id}/attestation
# --------------------------------------------------------------------------- #


async def test_attestation_passthrough_and_nonce_validation(client, seed_api_key, reset_forwarder_transport):
    seeded = await seed_api_key(credits=100_000)
    enclave = FakeEnclave()
    _m, mm_id = await seed_enclave("good.tee", e2ee_key=enclave.public_hex)
    product_routes.set_forwarder_transport(HostRouter({"good.tee": enclave.app}))

    nonce = "11" * 32
    resp = await client.get(f"/v1/e2e/instances/{mm_id}/attestation", params={"nonce": nonce},
                            headers=auth(seeded.raw_key))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"nonce": nonce, "mode": "dev", "public_bundle": {"e2ee_key": enclave.public_hex}}

    for bad in ("zz" * 32, "11" * 8, "11" * 65, "111"):
        resp = await client.get(f"/v1/e2e/instances/{mm_id}/attestation", params={"nonce": bad},
                                headers=auth(seeded.raw_key))
        assert resp.status_code == 400 and resp.headers[H_ERROR] == "bad_metadata", bad

    resp = await client.get(f"/v1/e2e/instances/{uuid.uuid4()}/attestation", params={"nonce": nonce},
                            headers=auth(seeded.raw_key))
    assert resp.status_code == 503 and resp.headers[H_ERROR] == "instance_unavailable"


# --------------------------------------------------------------------------- #
# POST /v1/e2e/invoke
# --------------------------------------------------------------------------- #


async def _setup_one(client, seed_api_key, **enclave_kwargs):
    seeded = await seed_api_key(credits=100_000)
    enclave = FakeEnclave(**enclave_kwargs)
    miner_id, _mm = await seed_enclave("good.tee", e2ee_key=enclave.public_hex)
    product_routes.set_forwarder_transport(HostRouter({"good.tee": enclave.app}))
    instance = (await list_instances(client, seeded.raw_key)).json()["instances"][0]
    return seeded, enclave, miner_id, instance


async def test_invoke_non_stream_roundtrip_settles_enclave_usage(client, seed_api_key, reset_forwarder_transport):
    seeded, enclave, miner_id, instance = await _setup_one(client, seed_api_key, prompt_tokens=13,
                                                           completion_tokens=7)
    before = await remaining_tokens(seeded.user_id)

    resp, meta, response_key, _blob = await invoke(client, seeded.raw_key, instance, request_id="rid-e2ee-1")
    assert resp.status_code == 200, resp.text
    assert resp.headers[H_ENCRYPTED] == "1"
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-request-id"] == "rid-e2ee-1"
    assert json.loads(resp.headers[H_USAGE]) == enclave.usage()
    opened = json.loads(open_response(response_key, meta, 200, resp.content))
    assert opened["choices"][0]["message"]["content"] == "Hello!"
    assert opened["echo_messages"] == [{"role": "user", "content": "secret prompt"}]

    # The relay forwarded the bound metadata (minus the routing-only instance id)
    # and the billing request id, with the enclave inference token.
    assert enclave.seen_headers["x-e2ee-model"] == MODEL
    assert "x-e2ee-instance" not in enclave.seen_headers
    assert enclave.seen_headers["x-request-id"] == "rid-e2ee-1"

    # Platform receipt: the same inference-receipt/v1 the plaintext path returns.
    raw = resp.headers["X-Talaris-Receipt"]
    receipt = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert receipt["request_id"] == "rid-e2ee-1" and receipt["model_id"] == MODEL

    (event,) = await events()
    assert event.status == "settled"
    assert (event.prompt_tokens, event.completion_tokens) == (13, 7)
    assert event.miner_id == miner_id
    expected = charged_token_count(20, Decimal(2))
    assert event.charged_tokens == expected == 40
    assert before - await remaining_tokens(seeded.user_id) == expected

    async with async_session_maker() as session:
        (stat,) = (await session.execute(select(MinerRequestStat))).scalars()
        assert stat.outcome == "success" and stat.request_id == "rid-e2ee-1"
        assert (stat.prompt_tokens, stat.completion_tokens) == (13, 7)


async def test_invoke_stream_roundtrip_settles_enclave_usage(client, seed_api_key, reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key, prompt_tokens=21,
                                                         completion_tokens=3)
    before = await remaining_tokens(seeded.user_id)
    resp, meta, response_key, _ = await invoke(client, seeded.raw_key, instance, stream=True)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers[H_ENCRYPTED] == "1"
    text, trailer = open_stream(resp.text, response_key, meta)
    assert text == "Hello!"
    assert trailer == {"usage": enclave.usage()}

    (event,) = await events()
    assert event.status == "settled"
    assert (event.prompt_tokens, event.completion_tokens) == (21, 3)
    assert event.charged_tokens == charged_token_count(24, Decimal(2))
    assert before - await remaining_tokens(seeded.user_id) == event.charged_tokens


async def test_invoke_stream_without_usage_bills_relayed_chunks(client, seed_api_key, reset_forwarder_transport):
    """HYP-56 rule on the E2EE path: no usage event -> ~1 token per sealed chunk."""
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key, chunks=("a", "b", "c", "d"))
    enclave.include_usage = False
    resp, meta, response_key, blob = await invoke(client, seeded.raw_key, instance, stream=True, max_tokens=50)
    assert resp.status_code == 200
    (event,) = await events()
    assert event.status == "settled"
    assert event.completion_tokens == 4
    assert event.prompt_tokens == len(blob)  # the reservation's ciphertext-length estimate


async def test_invoke_split_sse_lines_still_capture_usage():
    """The relay's SSE parser buffers across network reads."""
    from inference_api.routing.forwarder import EnclaveExchange

    exchange = EnclaveExchange(candidate=None, response=None, client=None)
    payload = b'data: {"e2ee":"QUJD","seq":0}\n\ndata: {"usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}\n\n'
    for i in range(0, len(payload), 7):
        exchange._accumulate(payload[i:i + 7])
    assert exchange.content_chunks == 1
    assert (exchange.usage.prompt_tokens, exchange.usage.completion_tokens) == (5, 2)


async def test_invoke_replayed_nonce_passes_409_through_without_charge(client, seed_api_key, reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    before = await remaining_tokens(seeded.user_id)
    first, *_ = await invoke(client, seeded.raw_key, instance)
    assert first.status_code == 200
    charged = before - await remaining_tokens(seeded.user_id)

    replay, *_ = await invoke(client, seeded.raw_key, instance, request_id=None)  # same nonce
    assert replay.status_code == 409
    assert replay.headers[H_ERROR] == "nonce_unknown"
    assert replay.headers[H_RETRY] == "1"
    assert replay.json()["error"]["code"] == "nonce_unknown"
    # The second call was reserved then released: nothing more charged.
    assert before - await remaining_tokens(seeded.user_id) == charged
    statuses = sorted(e.status for e in await events())
    assert statuses == ["released", "settled"]
    # A client-caused protocol error must not count against the miner.
    async with async_session_maker() as session:
        outcomes = [s.outcome for s in (await session.execute(select(MinerRequestStat))).scalars()]
        assert outcomes == ["success"]


async def test_invoke_reused_request_id_gets_fresh_billing_id(client, seed_api_key, reset_forwarder_transport):
    """A retry with the same X-Request-Id must not reuse (or ride free on) the old event."""
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    first, *_ = await invoke(client, seeded.raw_key, instance, request_id="same-id")
    second, *_ = await invoke(client, seeded.raw_key, instance, nonce=instance["nonces"][1], request_id="same-id")
    assert first.status_code == second.status_code == 200
    assert first.headers["X-Talaris-Request-Id"] == "same-id"
    assert second.headers["X-Talaris-Request-Id"] != "same-id"
    assert [e.status for e in await events()] == ["settled", "settled"]


async def test_invoke_ineligible_instance_is_503_retry(client, seed_api_key, reset_forwarder_transport):
    seeded, enclave, miner_id, instance = await _setup_one(client, seed_api_key)
    async with async_session_maker() as session:
        miner = await session.get(Miner, miner_id)
        miner.health = "unhealthy"
        await session.commit()
    resp, *_ = await invoke(client, seeded.raw_key, instance)
    assert resp.status_code == 503
    assert resp.headers[H_ERROR] == "instance_unavailable" and resp.headers[H_RETRY] == "1"
    assert enclave.invocations == 0 and await events() == []

    # Unknown / malformed instance ids, and an instance that serves another model.
    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        resp, *_ = await invoke(client, seeded.raw_key, {**instance, "instance_id": bad})
        assert resp.status_code == 503 and resp.headers[H_ERROR] == "instance_unavailable"
    _m, other = await seed_enclave("other.tee", e2ee_key=enclave.public_hex, model_id="small-model")
    resp, *_ = await invoke(client, seeded.raw_key, {**instance, "instance_id": str(other)})
    assert resp.status_code == 503 and resp.headers[H_ERROR] == "instance_unavailable"


async def test_invoke_unreachable_instance_releases_and_records_failure(client, seed_api_key,
                                                                         reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    product_routes.set_forwarder_transport(HostRouter({}))  # enclave went away after listing
    before = await remaining_tokens(seeded.user_id)
    resp, *_ = await invoke(client, seeded.raw_key, instance)
    assert resp.status_code == 503
    assert resp.headers[H_ERROR] == "instance_unavailable" and resp.headers[H_RETRY] == "1"
    assert [e.status for e in await events()] == ["released"]
    assert await remaining_tokens(seeded.user_id) == before
    async with async_session_maker() as session:
        (stat,) = (await session.execute(select(MinerRequestStat))).scalars()
        assert stat.outcome == "forward_error"


async def test_invoke_pin_unavailable_releases_reservation(client, seed_api_key, monkeypatch,
                                                           reset_forwarder_transport):
    """Pinning enforced + no attested fingerprint -> 503 retry, reservation released."""
    seeded = await seed_api_key(credits=100_000)
    enclave = FakeEnclave()
    _m, mm_id = await seed_enclave("nopin.tee", e2ee_key=enclave.public_hex, tls_cert_fingerprint=None)
    product_routes.set_forwarder_transport(None)
    monkeypatch.setattr(settings, "tls_pin_enforce", True)
    instance = {"instance_id": str(mm_id), "e2ee_key": enclave.public_hex, "nonces": ["22" * 32]}
    resp, *_ = await invoke(client, seeded.raw_key, instance)
    assert resp.status_code == 503 and resp.headers[H_ERROR] == "instance_unavailable"
    assert [e.status for e in await events()] == ["released"]


async def test_invoke_enforces_key_model_allowlist(client, seed_api_key, reset_forwarder_transport):
    _seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    scoped = await seed_api_key(raw_key="sk_scoped", credits=100_000, allowed_models=["demo-chat-001"])
    resp, *_ = await invoke(client, scoped.raw_key, instance)
    assert resp.status_code == 403
    assert enclave.invocations == 0


async def test_invoke_max_tokens_cap_and_metadata_errors(client, seed_api_key, reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    resp, *_ = await invoke(client, seeded.raw_key, instance, max_tokens=4097)
    assert resp.status_code == 400 and resp.headers[H_ERROR] == "bad_metadata"

    meta = RequestMeta(nonce=instance["nonces"][0], path="/v1/chat/completions", model=MODEL,
                       stream=False, max_tokens=10)
    headers = {**auth(seeded.raw_key), **meta.to_headers()}
    # Missing X-E2EE-Instance.
    resp = await client.post("/v1/e2e/invoke", content=b"x" * 2000, headers=headers)
    assert resp.status_code == 400 and resp.headers[H_ERROR] == "bad_metadata"
    # Unsupported version / path straight from RequestMeta.from_headers.
    resp = await client.post("/v1/e2e/invoke", content=b"x",
                             headers={**headers, H_INSTANCE: instance["instance_id"], "X-E2EE-Version": "v9"})
    assert resp.status_code == 400 and resp.headers[H_ERROR] == "unsupported_version"
    resp = await client.post("/v1/e2e/invoke", content=b"x",
                             headers={**headers, H_INSTANCE: instance["instance_id"], "X-E2EE-Path": "/v1/embeddings"})
    assert resp.status_code == 400 and resp.headers[H_ERROR] == "path_not_allowed"
    assert enclave.invocations == 0 and await events() == []


async def test_invoke_body_size_cap(client, seed_api_key, monkeypatch, reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    monkeypatch.setattr(settings, "e2ee_max_body_bytes", 4096)
    resp, *_ = await invoke(client, seeded.raw_key, instance,
                            body={"model": MODEL, "stream": False,
                                  "messages": [{"role": "user", "content": secrets.token_hex(8192)}]})
    assert resp.status_code == 413 and resp.headers[H_ERROR] == "payload_too_large"
    assert enclave.invocations == 0 and await events() == []


async def test_invoke_sealed_engine_error_without_usage_is_released(client, seed_api_key,
                                                                     reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    enclave.engine_status = 500
    enclave.include_usage = False
    resp, meta, response_key, _ = await invoke(client, seeded.raw_key, instance)
    assert resp.status_code == 500 and resp.headers[H_ENCRYPTED] == "1"
    open_response(response_key, meta, 500, resp.content)  # status is authenticated end to end
    assert [e.status for e in await events()] == ["released"]


async def test_invoke_sealed_200_without_usage_settles_on_reservation(client, seed_api_key,
                                                                       reset_forwarder_transport):
    seeded, enclave, _miner, instance = await _setup_one(client, seed_api_key)
    enclave.include_usage = False
    resp, meta, _key, blob = await invoke(client, seeded.raw_key, instance, max_tokens=30)
    assert resp.status_code == 200
    (event,) = await events()
    assert event.status == "settled"
    assert (event.prompt_tokens, event.completion_tokens) == (len(blob), 30)


async def test_plaintext_chat_completions_unchanged(client, seed_api_key, reset_forwarder_transport):
    """The plaintext path still serves and settles alongside an E2EE-capable enclave."""
    seeded = await seed_api_key(credits=100_000)

    class PlainEngine(FakeEnclave):
        def _build(self):
            app = super()._build()

            @app.post("/v1/chat/completions")
            async def chat():
                return {"choices": [{"message": {"role": "assistant", "content": "plain"}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}

            return app

    enclave = PlainEngine()
    await seed_enclave("good.tee", e2ee_key=enclave.public_hex, model_id="demo-chat-001")
    product_routes.set_forwarder_transport(HostRouter({"good.tee": enclave.app}))
    resp = await client.post("/v1/chat/completions", headers=auth(seeded.raw_key),
                             json={"model": "demo-chat-001", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "plain"
    assert resp.json()["usage"]["total_tokens"] == 6


# --------------------------------------------------------------------------- #
# PinError regression (plaintext streaming path)
# --------------------------------------------------------------------------- #


async def test_plaintext_stream_pin_unavailable_releases_reservation(client, seed_api_key, seed_miner,
                                                                     seed_provisioned_token, monkeypatch,
                                                                     reset_forwarder_transport):
    """A PinError from the pinned-client build used to escape ``except ForwardError``
    in _routed_stream_response AFTER the reservation was committed, leaking it until
    TTL (and 500ing). It is now a ForwardError: failover/503 + released."""
    seeded = await seed_api_key(credits=100_000)
    miner = await seed_miner(tls_cert_fingerprint=None)
    await seed_provisioned_token(miner_id=miner.miner_id, model_id="demo-chat-001")
    product_routes.set_forwarder_transport(None)
    monkeypatch.setattr(settings, "tls_pin_enforce", True)
    resp = await client.post(
        "/v1/chat/completions",
        headers=auth(seeded.raw_key),
        json={"model": "demo-chat-001", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "forward_failed"
    assert [e.status for e in await events()] == ["released"]
    async with async_session_maker() as session:
        (stat,) = (await session.execute(select(MinerRequestStat))).scalars()
        assert stat.outcome == "no_pin"


# --------------------------------------------------------------------------- #
# Provisioner persists the attested E2EE key
# --------------------------------------------------------------------------- #


async def test_provisioner_persists_and_clears_e2ee_public_key(monkeypatch):
    """``e2ee_public_key`` is exactly ``VerificationResult.e2ee_key_hex``, overwritten on
    every (re-)attestation -- including back to None."""
    from _tee import bind_dev_tee, dev_tee_app
    from nacl.signing import SigningKey
    from talaris_contracts import AsyncTeeClient

    from inference_api.provisioner import service as provisioner_service

    real_verify = provisioner_service.verify_attestation
    forced: dict = {}

    def verify(doc, expected, nonce):
        result = real_verify(doc, expected, nonce)
        if "key" in forced:
            result.e2ee_key_hex = forced["key"]
        return result

    monkeypatch.setattr(provisioner_service, "verify_attestation", verify)

    async with dev_tee_app() as (transport, base_url):
        hotkey = await bind_dev_tee(transport, base_url, SigningKey.generate())
        async with async_session_maker() as session:
            miner = Miner(id=uuid.uuid4(), hotkey=hotkey, tee_endpoint=base_url, attestation_status="pending",
                          health="healthy", last_seen=utcnow(), registered_at=utcnow())
            mm = MinerModel(id=uuid.uuid4(), miner_id=miner.id, model_id="demo-chat-001", loaded=True,
                            last_advertised_at=utcnow())
            session.add_all([miner, mm])
            await session.commit()
            miner_id, mm_id = miner.id, mm.id

        def factory(_endpoint):
            return AsyncTeeClient(client=httpx.AsyncClient(base_url=base_url, transport=transport))

        async def provision():
            async with async_session_maker() as session:
                await provisioner_service.ProvisionerService(session, tee_client_factory=factory).provision_miner(
                    miner_id
                )
                await session.commit()
            async with async_session_maker() as session:
                return (await session.get(MinerModel, mm_id)).e2ee_public_key

        # Whatever the real (dev) enclave advertises is what lands -- the verified value.
        async with httpx.AsyncClient(base_url=base_url, transport=transport) as ac:
            bundle = (await ac.get("/attestation", params={"nonce": "00" * 32})).json()["public_bundle"]
        assert await provision() == bundle.get("e2ee_key")

        forced["key"] = "ab" * 1216
        assert await provision() == "ab" * 1216  # re-attestation picks up a new key
        forced["key"] = None
        assert await provision() is None  # enclave stopped advertising one -> cleared


# --------------------------------------------------------------------------- #
# REAL tee_wrapper roundtrip
# --------------------------------------------------------------------------- #

_REAL_E2EE_ROUTER = "/home/rizzo/talaris-inference/inference-subnet-miner/src/tee_wrapper/routers/e2ee.py"


@pytest.mark.skipif(not __import__("os").path.exists(_REAL_E2EE_ROUTER), reason="tee_wrapper has no E2EE router yet")
async def test_real_tee_wrapper_e2ee_roundtrip(client, seed_api_key, monkeypatch, reset_forwarder_transport):
    """provision (verifier binds the E2EE key) -> list -> attestation -> invoke
    (non-stream + stream) against the REAL in-process tee_wrapper. Only the engine
    behind the enclave is faked (the dev TEE has none): the enclave's own module-level
    ``httpx`` is swapped for one whose clients hit a MockTransport engine."""
    import types

    from _tee import dev_tee_app
    from test_routing_forward import _provision_real_miner
    from tee_wrapper.proxy import inference as tee_proxy

    engine_usage = {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22}

    def engine(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("stream"):
            events = [
                {"choices": [{"index": 0, "delta": {"content": piece}}]} for piece in ("real ", "enclave")
            ] + [{"choices": [], "usage": engine_usage}]
            sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "real enclave"}}],
            "usage": engine_usage, "seen": body["messages"],
        })

    fake_httpx = types.SimpleNamespace(
        **{name: getattr(httpx, name) for name in dir(httpx) if not name.startswith("_")}
    )
    fake_httpx.AsyncClient = lambda *a, **kw: httpx.AsyncClient(
        *a, **{**kw, "transport": httpx.MockTransport(engine)}
    )
    monkeypatch.setattr(tee_proxy, "httpx", fake_httpx)

    seeded = await seed_api_key(credits=100_000)
    async with dev_tee_app() as (transport, base_url):
        supervisor = transport.app.state.supervisor
        monkeypatch.setattr(supervisor, "is_ready", lambda: True)
        supervisor.info.engine, supervisor.info.model = "mock", "demo-chat-001"

        miner_id, _hotkey = await _provision_real_miner(transport, base_url)
        enclave_key = transport.app.state.identity.e2ee_pub_bytes.hex()
        async with async_session_maker() as session:
            (mm,) = (await session.execute(select(MinerModel).where(MinerModel.miner_id == miner_id))).scalars()
            assert mm.e2ee_public_key == enclave_key  # persisted from the VERIFIED result
        product_routes.set_forwarder_transport(transport)

        listing = await list_instances(client, seeded.raw_key, model="demo-chat-001", nonces=3)
        assert listing.status_code == 200, listing.text
        (instance,) = listing.json()["instances"]
        assert instance["e2ee_key"] == enclave_key and len(instance["nonces"]) == 3

        nonce = "33" * 32
        att = await client.get(f"/v1/e2e/instances/{instance['instance_id']}/attestation",
                               params={"nonce": nonce}, headers=auth(seeded.raw_key))
        assert att.status_code == 200, att.text
        assert att.json()["public_bundle"]["e2ee_key"] == enclave_key

        resp, meta, response_key, _ = await invoke(client, seeded.raw_key, instance, model="demo-chat-001")
        assert resp.status_code == 200, resp.text
        opened = json.loads(open_response(response_key, meta, 200, resp.content))
        assert opened["choices"][0]["message"]["content"] == "real enclave"
        assert opened["seen"] == [{"role": "user", "content": "secret prompt"}]

        resp, meta, response_key, _ = await invoke(client, seeded.raw_key, instance, model="demo-chat-001",
                                                   nonce=instance["nonces"][1], stream=True)
        assert resp.status_code == 200, resp.text
        text, trailer = open_stream(resp.text, response_key, meta)
        assert text == "real enclave"
        assert trailer["usage"]["completion_tokens"] == 5

        # Replay against the real enclave: 409 nonce_unknown passed through.
        replay, *_ = await invoke(client, seeded.raw_key, instance, model="demo-chat-001")
        assert replay.status_code == 409 and replay.headers[H_ERROR] == "nonce_unknown"

    settled = [e for e in await events() if e.status == "settled"]
    assert len(settled) == 2
    for event in settled:
        assert (event.prompt_tokens, event.completion_tokens) == (17, 5)
        assert event.charged_tokens == 22  # demo-chat-001 multiplier 1
        assert event.miner_id == miner_id
    assert sorted(e.status for e in await events()) == ["released", "settled", "settled"]
