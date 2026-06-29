#!/usr/bin/env python3
"""Genuine cross-process end-to-end test of the Talaris serving plane.

Stands up REAL separate processes over REAL HTTP and pushes a REAL inference
completion through the real dev-mode TEE, asserting real billing:

    developer -> register -> provision -> engine -> inference -> settle

Processes (all real OS processes, NOT in-process ASGI):
  * Redis            : docker container (replay guard / rate limit)
  * TEE producer     : uvicorn `tee_wrapper.app:app` in DEV mode (plain http :8801)
  * inference-api    : uvicorn `inference_api.main:create_app --factory` (:8800)

The platform provisioner's background loop auto-attests the registered miner
(real /attestation -> verify -> /provision over HTTP) and, via the small
PROVISIONER_START_ENGINE enhancement, drives the TEE's /engine/start so the
bundled mock engine serves real completions through the real proxy.

Run with the inference-api venv python. The driver itself only needs
talaris_contracts + httpx + nacl (all in that venv); it spawns the two servers
as subprocesses with the right env/PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from nacl.signing import SigningKey

# --------------------------------------------------------------------------- #
# Fixed paths / constants
# --------------------------------------------------------------------------- #
MINER_SRC = "/home/rizzo/talaris-inference/inference-subnet-miner/src"
INFERENCE_API_PKG = (
    "/home/rizzo/talaris-inference/inference-api/inference-mono/inference_api"
)
VENV_PY = "/home/rizzo/talaris-inference/inference-api/.venv/bin/python"

REDIS_CONTAINER = "talaris_e2e_redis"
REDIS_PORT = 6379
REDIS_DB = 15
TEE_PORT = 8801
API_PORT = 8800

TEE_BASE = f"http://127.0.0.1:{TEE_PORT}"
API_BASE = f"http://127.0.0.1:{API_PORT}"

# Same fixed platform provisioner seed as inference_api/tests/conftest.py + _tee.py
PLATFORM_PROVISIONER_SEED = bytes(range(32))
PLATFORM_PROVISIONER_SIGNING_KEY = SigningKey(PLATFORM_PROVISIONER_SEED)
PLATFORM_PROVISIONER_VERIFY_KEY_HEX = bytes(
    PLATFORM_PROVISIONER_SIGNING_KEY.verify_key
).hex()

MODEL_ID = "mock-model"
SK_KEY = "sk_e2e_developer_key"
RK_MINER_TOKEN = "rk_e2e_miner_token"
USER_CREDITS = 1_000_000  # plenty of credits for a couple of requests

# Mock engine's deterministic usage (tee_wrapper/testing/mock_engine.py)
EXPECTED_CONTENT = "Hello from the mock engine."
EXPECTED_PROMPT_TOKENS = 7
EXPECTED_COMPLETION_TOKENS = 5
EXPECTED_TOTAL_TOKENS = 12


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


class Proc:
    def __init__(self, name: str, popen: subprocess.Popen, logfile):
        self.name = name
        self.popen = popen
        self.logfile = logfile


def wait_http_ok(url: str, timeout: float = 40.0, name: str = "") -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                log(f"{name or url} healthy ({r.status_code})")
                return
            last = f"status={r.status_code}"
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
        time.sleep(0.4)
    raise RuntimeError(f"timed out waiting for {name or url}: {last}")


# --------------------------------------------------------------------------- #
# Redis (docker)
# --------------------------------------------------------------------------- #
def start_redis() -> None:
    subprocess.run(
        ["docker", "rm", "-f", REDIS_CONTAINER],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log("starting redis container...")
    subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "-p", f"{REDIS_PORT}:6379",
            "--name", REDIS_CONTAINER,
            "redis:7",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    # Wait for redis to accept connections.
    deadline = time.time() + 30
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "exec", REDIS_CONTAINER, "redis-cli", "ping"],
            capture_output=True, text=True,
        )
        if out.stdout.strip() == "PONG":
            log("redis PONG")
            return
        time.sleep(0.4)
    raise RuntimeError("redis did not become ready")


def stop_redis() -> None:
    subprocess.run(
        ["docker", "rm", "-f", REDIS_CONTAINER],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log("redis container removed")


# --------------------------------------------------------------------------- #
# TEE producer + inference-api servers
# --------------------------------------------------------------------------- #
def start_tee(tmp: Path, logdir: Path) -> Proc:
    env = os.environ.copy()
    env["PYTHONPATH"] = MINER_SRC
    env.update(
        {
            "TEE_DEV_MODE": "true",
            "TEE_ATTESTATION_MODE": "dev",
            "TEE_ALLOW_DEV_MODE": "true",
            "TEE_TLS_ENABLED": "false",
            "TEE_REQUIRE_GPU": "false",
            "TEE_VERIFY_MODEL_INTEGRITY": "false",
            "TEE_MINER_KEY_TYPE": "ed25519",
            "TEE_VALIDATOR_STATIC_KEYS": PLATFORM_PROVISIONER_VERIFY_KEY_HEX,
            "TEE_USAGE_DB_PATH": str(tmp / "usage.db"),
        }
    )
    logf = open(logdir / "tee.log", "w")
    log(f"starting TEE producer on :{TEE_PORT} (dev mode, plain http)...")
    popen = subprocess.Popen(
        [
            VENV_PY, "-m", "uvicorn", "tee_wrapper.app:app",
            "--host", "127.0.0.1", "--port", str(TEE_PORT),
            "--log-level", "info",
        ],
        env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    return Proc("tee", popen, logf)


def start_api(tmp: Path, logdir: Path) -> Proc:
    fernet_key = Fernet.generate_key().decode()
    env = os.environ.copy()
    # The inference_api package lives under this dir as `inference_api/...`.
    env["PYTHONPATH"] = INFERENCE_API_PKG
    db_path = tmp / "inference_api.sqlite"
    env.update(
        {
            "APP_ENV": "test",
            "DEBUG": "false",
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "REDIS_URL": f"redis://localhost:{REDIS_PORT}/{REDIS_DB}",
            "RATE_LIMIT_ENABLED": "false",
            "RATE_LIMIT_FAIL_OPEN": "true",
            "SECRET_PEPPER": "e2e-pepper-secret-value-32-bytes-long!!",
            "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
            "CORS_ORIGINS": "",
            # --- serving / coordination plane ---
            "PROVISIONER_ENABLED": "true",
            # The setting's validator enforces a 5s floor (PROVISIONER_LOOP_INTERVAL_SECONDS ge 5).
            "PROVISIONER_LOOP_INTERVAL_SECONDS": "5",
            "PROVISIONER_START_ENGINE": "true",  # the enhancement under test
            "PROVISIONER_ENGINE_NAME": "vllm",
            "CHAIN_REQUIRED": "false",
            "ALLOW_DEV_ATTESTATION": "true",
            "REGISTRATION_KEY_TYPE": "ed25519",
            "TLS_PIN_ENFORCE": "false",
            "PROVISIONER_SIGNING_KEY_HEX": PLATFORM_PROVISIONER_SEED.hex(),
            "PROVISIONER_VERIFY_KEY_HEX": PLATFORM_PROVISIONER_VERIFY_KEY_HEX,
            "PROVISIONER_TOKEN_ENCRYPTION_KEY": fernet_key,
        }
    )

    # Create the DB schema in-process FIRST (same env as the server).
    log("creating inference-api DB schema...")
    schema = subprocess.run(
        [VENV_PY, str(Path(__file__).parent / "create_schema.py")],
        env=env, capture_output=True, text=True,
    )
    if schema.returncode != 0:
        raise RuntimeError(
            f"schema creation failed:\nSTDOUT:{schema.stdout}\nSTDERR:{schema.stderr}"
        )
    log(schema.stdout.strip() or "schema created")

    logf = open(logdir / "api.log", "w")
    log(f"starting inference-api on :{API_PORT} (PROVISIONER_ENABLED, START_ENGINE)...")
    popen = subprocess.Popen(
        [
            VENV_PY, "-m", "uvicorn", "inference_api.main:create_app",
            "--factory", "--host", "127.0.0.1", "--port", str(API_PORT),
            "--log-level", "info",
        ],
        env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    # stash the env so the driver can seed the same DB.
    popen._e2e_env = env  # type: ignore[attr-defined]
    return Proc("api", popen, logf)


# --------------------------------------------------------------------------- #
# Seed the inference-api DB directly (it owns it).
# --------------------------------------------------------------------------- #
SEED_SCRIPT = r'''
import asyncio, uuid, sys
from datetime import datetime, timezone

from inference_api.db import async_session_maker
from inference_api.models import (
    APIKey, ManualTokenAdjustment, ServiceClient, ServiceClientRole, User,
)
from inference_api.security import keyed_hash

SK_KEY = sys.argv[1]
RK_MINER_TOKEN = sys.argv[2]
CREDITS = int(sys.argv[3])


def utcnow():
    return datetime.now(timezone.utc)


async def main():
    async with async_session_maker() as session:
        user = User(id=uuid.uuid4(), is_active=True, created_at=utcnow())
        api_key = APIKey(
            id=uuid.uuid4(),
            user_id=user.id,
            hashed_key=keyed_hash(SK_KEY),
            scopes=["models:read", "inference:write", "usage:read"],
            rate_limit_per_minute=0,
        )
        adjustment = ManualTokenAdjustment(
            id=uuid.uuid4(), user_id=user.id, amount=CREDITS, created_at=utcnow(),
        )
        miner_client = ServiceClient(
            id=uuid.uuid4(),
            role=ServiceClientRole.miner,
            hashed_token=keyed_hash(RK_MINER_TOKEN),
            rate_limit_per_minute=0,
        )
        session.add_all([user, api_key, adjustment, miner_client])
        await session.commit()
        print(f"SEEDED user_id={user.id} api_key_id={api_key.id} miner_client_id={miner_client.id}")


asyncio.run(main())
'''


def seed_db(api_env: dict) -> None:
    log("seeding inference-api DB (developer User+credits, sk_ APIKey, miner ServiceClient)...")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(SEED_SCRIPT)
        seed_path = f.name
    out = subprocess.run(
        [VENV_PY, seed_path, SK_KEY, RK_MINER_TOKEN, str(USER_CREDITS)],
        env=api_env, capture_output=True, text=True,
    )
    os.unlink(seed_path)
    if out.returncode != 0:
        raise RuntimeError(f"seed failed:\nSTDOUT:{out.stdout}\nSTDERR:{out.stderr}")
    log(out.stdout.strip())


# --------------------------------------------------------------------------- #
# DB query helper (run a snippet in the api env, return parsed JSON on a line).
# --------------------------------------------------------------------------- #
def db_query(api_env: dict, snippet: str, *args: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(snippet)
        path = f.name
    out = subprocess.run(
        [VENV_PY, path, *args], env=api_env, capture_output=True, text=True
    )
    os.unlink(path)
    if out.returncode != 0:
        raise RuntimeError(f"db query failed:\nSTDOUT:{out.stdout}\nSTDERR:{out.stderr}")
    # The snippet prints a single JSON line prefixed with RESULT:
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise RuntimeError(f"no RESULT line in db query output:\n{out.stdout}")


MINER_STATE_SNIPPET = r'''
import asyncio, json, sys
from sqlalchemy import select
from inference_api.db import async_session_maker
from inference_api.models import Miner, MinerModel, ProvisionedToken

HOTKEY = sys.argv[1].lower()


async def main():
    async with async_session_maker() as session:
        miner = (await session.execute(
            select(Miner).where(Miner.hotkey == HOTKEY))).scalar_one_or_none()
        if miner is None:
            print("RESULT:" + json.dumps({"found": False}))
            return
        models = (await session.execute(
            select(MinerModel).where(MinerModel.miner_id == miner.id))).scalars().all()
        token = (await session.execute(
            select(ProvisionedToken).where(
                ProvisionedToken.miner_id == miner.id,
                ProvisionedToken.status == "active"))).scalar_one_or_none()
        print("RESULT:" + json.dumps({
            "found": True,
            "attestation_status": miner.attestation_status,
            "attestation_mode": miner.attestation_mode,
            "health": miner.health,
            "tee_endpoint": miner.tee_endpoint,
            "enclave_verify_key": miner.enclave_verify_key,
            "models": [
                {"model_id": m.model_id, "loaded": bool(m.loaded), "model_hash": m.model_hash}
                for m in models
            ],
            "has_active_token": token is not None,
            "has_admin_token": bool(token and token.admin_encrypted_token),
        }))


asyncio.run(main())
'''

BILLING_SNIPPET = r'''
import asyncio, json, sys
from sqlalchemy import select
from inference_api.db import async_session_maker
from inference_api.models import (
    InferenceUsageEvent, ManualTokenAdjustment, UsagePeriod, User,
)


async def main():
    async with async_session_maker() as session:
        events = (await session.execute(
            select(InferenceUsageEvent).order_by(InferenceUsageEvent.created_at))
        ).scalars().all()
        periods = (await session.execute(select(UsagePeriod))).scalars().all()
        out = {
            "events": [
                {
                    "status": e.status,
                    "model": e.model,
                    "miner_id": str(e.miner_id) if e.miner_id else None,
                    "miner_hotkey": e.miner_hotkey,
                    "prompt_tokens": e.prompt_tokens,
                    "completion_tokens": e.completion_tokens,
                    "raw_total_tokens": e.raw_total_tokens,
                    "charged_tokens": e.charged_tokens,
                    "settled_at": e.settled_at.isoformat() if e.settled_at else None,
                }
                for e in events
            ],
            "periods": [
                {
                    "total_allowance": p.total_allowance,
                    "used_tokens": p.used_tokens,
                    "remaining_tokens": p.remaining_tokens,
                }
                for p in periods
            ],
        }
        print("RESULT:" + json.dumps(out))


asyncio.run(main())
'''


# --------------------------------------------------------------------------- #
# TEE bind + miner registration (real HTTP).
# --------------------------------------------------------------------------- #
def bind_hotkey_into_tee(signing_key: SigningKey) -> str:
    """GET /attestation, build the bind message, sign, POST /bind. Returns hotkey hex."""
    from talaris_contracts.bind import BindRequest, bind_message

    hotkey_hex = bytes(signing_key.verify_key).hex()
    with httpx.Client(base_url=TEE_BASE, timeout=10.0) as c:
        att = c.get("/attestation", params={"nonce": "00" * 32})
        att.raise_for_status()
        doc = att.json()
        message = bind_message(doc["boot_nonce"], doc["fingerprint"])
        signature = signing_key.sign(message).signature.hex()
        req = BindRequest(miner_pubkey=hotkey_hex, signature=signature)
        resp = c.post("/bind", json=req.model_dump())
        resp.raise_for_status()
        log(f"bound hotkey into TEE: miner_hash={resp.json().get('miner')}")
    return hotkey_hex


def register_miner(signing_key: SigningKey) -> dict:
    """Build a signed SelfRegistration and POST /miners/register with the rk_ token."""
    from talaris_contracts import HostedModel, sign_registration_ed25519

    reg = sign_registration_ed25519(
        signing_key=signing_key,
        peer_id="e2e-peer-0001",
        subnet_node_id=11,
        tee_endpoint=TEE_BASE,
        hosted_models=[HostedModel(model_id=MODEL_ID)],
    )
    with httpx.Client(base_url=API_BASE, timeout=10.0) as c:
        resp = c.post(
            "/miners/register",
            json=reg.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {RK_MINER_TOKEN}"},
        )
    if resp.status_code != 201:
        raise RuntimeError(f"registration failed {resp.status_code}: {resp.text}")
    log(f"miner registered: {resp.json().get('attestation_status')} "
        f"models={resp.json().get('models')}")
    return resp.json()


def poll_me_until_attested(hotkey: str, timeout: float = 75.0) -> dict:
    """Poll GET /miners/me until attestation_status == attested."""
    deadline = time.time() + timeout
    last = None
    with httpx.Client(base_url=API_BASE, timeout=10.0) as c:
        while time.time() < deadline:
            resp = c.get(
                "/miners/me",
                params={"hotkey": hotkey},
                headers={"Authorization": f"Bearer {RK_MINER_TOKEN}"},
            )
            if resp.status_code == 200:
                me = resp.json()
                last = me
                if me.get("attestation_status") == "attested":
                    return me
            time.sleep(1.0)
    raise RuntimeError(f"miner never reached attested; last /miners/me={last}")


# --------------------------------------------------------------------------- #
# Developer inference calls.
# --------------------------------------------------------------------------- #
def developer_chat_completion() -> dict:
    with httpx.Client(base_url=API_BASE, timeout=30.0) as c:
        resp = c.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {SK_KEY}"},
            json={
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "ping"}],
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"chat/completions failed {resp.status_code}: {resp.text}")
    return resp.json()


def developer_chat_completion_stream() -> tuple[str, dict | None]:
    content_parts: list[str] = []
    usage = None
    with httpx.Client(base_url=API_BASE, timeout=30.0) as c:
        with c.stream(
            "POST",
            "/v1/chat/completions",
            headers={"X-API-Key": SK_KEY},
            json={
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "ping stream"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as resp:
            if resp.status_code != 200:
                body = resp.read().decode()
                raise RuntimeError(f"stream failed {resp.status_code}: {body}")
            # Fully drain the stream (do NOT break on [DONE]) so the server-side
            # StreamingResponse generator runs to completion and its `finally`
            # settles the reservation; an early break cancels that generator.
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if isinstance(delta.get("content"), str):
                        content_parts.append(delta["content"])
                if isinstance(obj.get("usage"), dict):
                    usage = obj["usage"]
    return "".join(content_parts), usage


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #
class AssertionFail(Exception):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionFail(msg)
    log(f"  OK: {msg}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def terminate(proc: Proc | None) -> None:
    if proc is None:
        return
    try:
        proc.popen.send_signal(signal.SIGINT)
        try:
            proc.popen.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.popen.terminate()
            try:
                proc.popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.popen.kill()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            proc.logfile.close()
        except Exception:  # noqa: BLE001
            pass


def dump_log(logdir: Path, name: str, tail: int = 40) -> None:
    p = logdir / name
    if not p.exists():
        return
    lines = p.read_text(errors="replace").splitlines()
    log(f"---- {name} (last {tail}) ----")
    for line in lines[-tail:]:
        print("   " + line, flush=True)
    log(f"---- end {name} ----")


def main() -> int:
    tee_proc: Proc | None = None
    api_proc: Proc | None = None
    tmpdir = tempfile.mkdtemp(prefix="talaris_e2e_")
    tmp = Path(tmpdir)
    logdir = tmp / "logs"
    logdir.mkdir()
    log(f"tmp dir: {tmp}")

    rc = 1
    try:
        # --- 1. start the three processes --- #
        start_redis()
        tee_proc = start_tee(tmp, logdir)
        api_proc = start_api(tmp, logdir)
        api_env = api_proc.popen._e2e_env  # type: ignore[attr-defined]

        wait_http_ok(f"{TEE_BASE}/healthz", name="TEE /healthz")
        wait_http_ok(f"{API_BASE}/health", name="inference-api /health")

        # --- 2. seed the inference-api DB --- #
        seed_db(api_env)

        # --- 3. bind the miner hotkey into the TEE --- #
        miner_signing_key = SigningKey.generate()
        hotkey = bind_hotkey_into_tee(miner_signing_key)

        # --- 4. miner self-registers over HTTP --- #
        register_miner(miner_signing_key)

        # --- 5. wait for the provisioner loop to attest + start the engine --- #
        log("polling /miners/me until attested (background provisioner loop)...")
        me = poll_me_until_attested(hotkey)
        log(f"miner attested: status={me['attestation_status']} mode={me.get('attestation_mode')}")

        # Confirm DB-level state: model loaded + active token (engine started).
        state = db_query(api_env, MINER_STATE_SNIPPET, hotkey)
        log(f"miner DB state: {json.dumps(state)}")
        check(state["attestation_status"] == "attested", "miner attestation_status == attested")
        check(state["health"] == "healthy", "miner health == healthy")
        check(state["has_active_token"], "miner has an active provisioned token")
        check(state["has_admin_token"], "active token carries an encrypted admin token")
        loaded_models = [m for m in state["models"] if m["model_id"] == MODEL_ID and m["loaded"]]
        check(bool(loaded_models), f"model {MODEL_ID!r} is loaded=True (engine started via enhancement)")

        # Confirm the TEE engine is actually READY (the enhancement drove /engine/start).
        with httpx.Client(base_url=TEE_BASE, timeout=10.0) as c:
            att = c.get("/attestation", params={"nonce": "00" * 32}).json()
        log(f"TEE attestation post-start: mode={att['mode']} miner={att['miner']} "
            f"model_hash={att.get('model_hash')}")

        # --- 6. developer inference (non-stream) --- #
        log("developer POST /v1/chat/completions (non-stream)...")
        completion = developer_chat_completion()
        content = completion["choices"][0]["message"]["content"]
        usage = completion.get("usage") or {}
        log(f"completion content = {content!r}")
        log(f"completion usage   = {json.dumps(usage)}")
        check(content == EXPECTED_CONTENT,
              f"completion content == {EXPECTED_CONTENT!r} (flowed api->real TEE proxy->mock engine)")
        check(bool(usage), "completion has a usage block")
        check(usage.get("prompt_tokens") == EXPECTED_PROMPT_TOKENS,
              f"usage.prompt_tokens == {EXPECTED_PROMPT_TOKENS}")
        check(usage.get("completion_tokens") == EXPECTED_COMPLETION_TOKENS,
              f"usage.completion_tokens == {EXPECTED_COMPLETION_TOKENS}")
        check(usage.get("total_tokens") == EXPECTED_TOTAL_TOKENS,
              f"usage.total_tokens == {EXPECTED_TOTAL_TOKENS}")

        # --- 7. assert billing for the non-stream request --- #
        billing = db_query(api_env, BILLING_SNIPPET)
        settled = [e for e in billing["events"] if e["status"] == "settled"]
        check(len(settled) >= 1, "at least one settled InferenceUsageEvent exists")
        ev = settled[0]
        check(ev["miner_id"] is not None, "settled event has miner_id set")
        check(ev["miner_hotkey"] == hotkey, "settled event miner_hotkey == registered hotkey")
        check(ev["prompt_tokens"] == EXPECTED_PROMPT_TOKENS,
              f"settled prompt_tokens == {EXPECTED_PROMPT_TOKENS} (real mock-engine usage)")
        check(ev["completion_tokens"] == EXPECTED_COMPLETION_TOKENS,
              f"settled completion_tokens == {EXPECTED_COMPLETION_TOKENS}")
        check(ev["raw_total_tokens"] == EXPECTED_TOTAL_TOKENS,
              f"settled raw_total_tokens == {EXPECTED_TOTAL_TOKENS}")
        check(ev["charged_tokens"] == EXPECTED_TOTAL_TOKENS,
              f"charged_tokens == {EXPECTED_TOTAL_TOKENS} (multiplier 1)")
        period = billing["periods"][0]
        remaining_after_one = period["remaining_tokens"]
        log(f"usage period after non-stream: used={period['used_tokens']} "
            f"remaining={remaining_after_one} allowance={period['total_allowance']}")
        check(period["used_tokens"] == EXPECTED_TOTAL_TOKENS,
              f"period used_tokens == {EXPECTED_TOTAL_TOKENS} (credits decreased by the charge)")
        check(remaining_after_one == period["total_allowance"] - EXPECTED_TOTAL_TOKENS,
              "remaining credits decreased by the charged amount")

        # --- 8. streaming request --- #
        log("developer POST /v1/chat/completions (stream:true)...")
        stream_content, stream_usage = developer_chat_completion_stream()
        log(f"streamed content = {stream_content!r}")
        log(f"streamed usage   = {json.dumps(stream_usage)}")
        check(stream_content == EXPECTED_CONTENT,
              f"streamed content == {EXPECTED_CONTENT!r}")
        check(stream_usage is not None
              and stream_usage.get("total_tokens") == EXPECTED_TOTAL_TOKENS,
              f"streamed usage.total_tokens == {EXPECTED_TOTAL_TOKENS}")

        # Streaming settles asynchronously in a finally; poll the DB for the 2nd settle.
        deadline = time.time() + 20
        billing2 = billing
        while time.time() < deadline:
            billing2 = db_query(api_env, BILLING_SNIPPET)
            settled2 = [e for e in billing2["events"] if e["status"] == "settled"]
            if len(settled2) >= 2:
                break
            time.sleep(1.0)
        settled2 = [e for e in billing2["events"] if e["status"] == "settled"]
        check(len(settled2) >= 2, "a second settled InferenceUsageEvent exists (stream settled)")
        period2 = billing2["periods"][0]
        log(f"usage period after stream: used={period2['used_tokens']} "
            f"remaining={period2['remaining_tokens']}")
        check(period2["used_tokens"] == 2 * EXPECTED_TOTAL_TOKENS,
              f"period used_tokens == {2 * EXPECTED_TOTAL_TOKENS} after two settled requests")
        check(period2["remaining_tokens"] == period2["total_allowance"] - 2 * EXPECTED_TOTAL_TOKENS,
              "remaining credits decreased by both charges")

        # --- 9. report PASS --- #
        print("", flush=True)
        print("=" * 70, flush=True)
        print("E2E PASS", flush=True)
        print("=" * 70, flush=True)
        print(f"  completion text     : {content!r}", flush=True)
        print(f"  streamed text       : {stream_content!r}", flush=True)
        print(f"  real engine usage   : prompt={ev['prompt_tokens']} "
              f"completion={ev['completion_tokens']} total={ev['raw_total_tokens']}", flush=True)
        print(f"  settled events      : {len(settled2)} (non-stream + stream)", flush=True)
        print(f"  charged per request : {ev['charged_tokens']} tokens (multiplier 1.0)", flush=True)
        print(f"  credits allowance   : {period2['total_allowance']}", flush=True)
        print(f"  credits used        : {period2['used_tokens']} "
              f"(= 2 x {EXPECTED_TOTAL_TOKENS})", flush=True)
        print(f"  credits remaining   : {period2['remaining_tokens']}", flush=True)
        print(f"  serving miner hotkey: {hotkey}", flush=True)
        print("=" * 70, flush=True)
        rc = 0
    except Exception as exc:  # noqa: BLE001
        log(f"E2E FAIL: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        dump_log(logdir, "tee.log")
        dump_log(logdir, "api.log")
        rc = 1
    finally:
        log("tearing down...")
        terminate(api_proc)
        terminate(tee_proc)
        stop_redis()
        log(f"logs preserved under {logdir}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
