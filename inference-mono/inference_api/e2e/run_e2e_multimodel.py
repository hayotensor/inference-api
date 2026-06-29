#!/usr/bin/env python3
"""Capstone cross-process E2E for the MULTI-MODEL serving + receipt/auth pipeline.

Extends the single-model harness (`run_e2e.py`) to a two-enclave topology and
proves per-model routing, per-model attestation/provisioning, the bound-miner
usage-receipts feed (WS-C / #9), request_id propagation, and the signed model
allowlist artifact (WS-F) -- all over REAL OS processes and REAL HTTP.

Processes (all real, NOT in-process ASGI):
  * Redis            : docker container (replay guard / rate limit)
  * TEE enclave A    : uvicorn `tee_wrapper.app:app` dev mode, :8801, own usage.db, model mock-model-a
  * TEE enclave B    : uvicorn `tee_wrapper.app:app` dev mode, :8802, own usage.db, model mock-model-b
  * inference-api    : uvicorn `inference_api.main:create_app --factory`, :8800

Flow:
  developer
    -> bind SAME miner hotkey into BOTH enclaves
    -> register ONCE with two HostedModels, each with its own tee_endpoint
    -> provisioner attests BOTH model-enclaves + starts both engines (per-model token)
    -> infer mock-model-a (routes to enclave A) + mock-model-b (routes to enclave B)
    -> prove routing via per-enclave usage_count deltas
    -> read each enclave's bound-miner /usage/receipts (metadata-only)
    -> cross-check receipt request_id == InferenceUsageEvent.request_id
    -> verify the signed /allowlist artifact

Run with the inference-api venv python (it has talaris_contracts + httpx + nacl).
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
# Fixed paths / constants  (verbatim from run_e2e.py)
# --------------------------------------------------------------------------- #
MINER_SRC = "/home/rizzo/talaris-inference/inference-subnet-miner/src"
INFERENCE_API_PKG = (
    "/home/rizzo/talaris-inference/inference-api/inference-mono/inference_api"
)
VENV_PY = "/home/rizzo/talaris-inference/inference-api/.venv/bin/python"

REDIS_CONTAINER = "talaris_e2e_mm_redis"
REDIS_PORT = 6379
REDIS_DB = 14
TEE_A_PORT = 8801
TEE_B_PORT = 8802
TEE_A_ENGINE_PORT = 8901  # distinct upstream mock-engine port per co-located enclave
TEE_B_ENGINE_PORT = 8902
API_PORT = 8800

TEE_A_BASE = f"http://127.0.0.1:{TEE_A_PORT}"
TEE_B_BASE = f"http://127.0.0.1:{TEE_B_PORT}"
API_BASE = f"http://127.0.0.1:{API_PORT}"

# Same fixed platform provisioner seed as inference_api/tests/conftest.py + _tee.py
PLATFORM_PROVISIONER_SEED = bytes(range(32))
PLATFORM_PROVISIONER_SIGNING_KEY = SigningKey(PLATFORM_PROVISIONER_SEED)
PLATFORM_PROVISIONER_VERIFY_KEY_HEX = bytes(
    PLATFORM_PROVISIONER_SIGNING_KEY.verify_key
).hex()

MODEL_A = "mock-model-a"
MODEL_B = "mock-model-b"
SK_KEY = "sk_e2e_developer_key"
RK_MINER_TOKEN = "rk_e2e_miner_token"
USER_CREDITS = 1_000_000  # plenty of credits for a couple of requests

# Mock engine's deterministic usage (tee_wrapper/testing/mock_engine.py).
# Both enclaves run the same bundled mock engine -> identical text/usage; routing
# is proven via per-enclave usage_count, NOT via differing content.
EXPECTED_CONTENT = "Hello from the mock engine."
EXPECTED_PROMPT_TOKENS = 7
EXPECTED_COMPLETION_TOKENS = 5
EXPECTED_TOTAL_TOKENS = 12

FORBIDDEN_RECEIPT_KEYS = {"messages", "prompt", "content"}


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
# Redis (docker)  (verbatim machinery from run_e2e.py)
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
def start_tee(port: int, engine_port: int, usage_db_path: Path, logdir: Path, label: str) -> Proc:
    """Generalized TEE launcher: one dev-mode enclave on `port` with its own usage.db.

    Each enclave gets a DISTINCT upstream mock-engine port (`engine_port`); in production
    every enclave is its own container with its own 127.0.0.1:8001, but co-locating two on
    one host requires distinct upstream ports so each proxy talks to its OWN engine.
    """
    env = os.environ.copy()
    # tee_wrapper now resolves to the canonical MINER_SRC; keep PYTHONPATH=MINER_SRC.
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
            "TEE_USAGE_DB_PATH": str(usage_db_path),
            "TEE_ENGINE_PORT": str(engine_port),
        }
    )
    logf = open(logdir / f"tee_{label}.log", "w")
    log(f"starting TEE enclave {label.upper()} on :{port} (dev mode, plain http, usage={usage_db_path.name})...")
    popen = subprocess.Popen(
        [
            VENV_PY, "-m", "uvicorn", "tee_wrapper.app:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "info",
        ],
        env=env, stdout=logf, stderr=subprocess.STDOUT,
    )
    return Proc(f"tee_{label}", popen, logf)


def start_api(tmp: Path, logdir: Path) -> Proc:
    fernet_key = Fernet.generate_key().decode()
    env = os.environ.copy()
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
            "PROVISIONER_LOOP_INTERVAL_SECONDS": "5",  # validator floor is 5s
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
    popen._e2e_env = env  # type: ignore[attr-defined]
    return Proc("api", popen, logf)


# --------------------------------------------------------------------------- #
# Seed the inference-api DB directly (verbatim from run_e2e.py).
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
# DB query helper (verbatim from run_e2e.py).
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
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise RuntimeError(f"no RESULT line in db query output:\n{out.stdout}")


# PER-MODEL miner state: each MinerModel's attestation_status/tee_endpoint/loaded
# plus whether an active ProvisionedToken exists per (miner, model_id).
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
        tokens = (await session.execute(
            select(ProvisionedToken).where(
                ProvisionedToken.miner_id == miner.id,
                ProvisionedToken.status == "active"))).scalars().all()
        tok_by_model = {
            t.model_id: {
                "token_id": str(t.id),
                "has_admin_token": bool(t.admin_encrypted_token),
                "key_id": t.key_id,
            }
            for t in tokens
        }
        print("RESULT:" + json.dumps({
            "found": True,
            "attestation_status": miner.attestation_status,
            "attestation_mode": miner.attestation_mode,
            "health": miner.health,
            "tee_endpoint": miner.tee_endpoint,
            "models": [
                {
                    "model_id": m.model_id,
                    "attestation_status": m.attestation_status,
                    "attestation_mode": m.attestation_mode,
                    "tee_endpoint": m.tee_endpoint,
                    "loaded": bool(m.loaded),
                    "model_hash": m.model_hash,
                    "has_active_token": m.model_id in tok_by_model,
                    "has_admin_token": tok_by_model.get(m.model_id, {}).get("has_admin_token", False),
                    "token_id": tok_by_model.get(m.model_id, {}).get("token_id"),
                    "token_key_id": tok_by_model.get(m.model_id, {}).get("key_id"),
                }
                for m in models
            ],
        }))


asyncio.run(main())
'''

# Billing snippet -- adds request_id + model so we can cross-check per-model.
BILLING_SNIPPET = r'''
import asyncio, json, sys
from sqlalchemy import select
from inference_api.db import async_session_maker
from inference_api.models import InferenceUsageEvent, UsagePeriod


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
                    "request_id": e.request_id,
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
def bind_hotkey_into_tee(signing_key: SigningKey, base_url: str) -> str:
    """GET /attestation, build the bind message, sign, POST /bind. Returns hotkey hex."""
    from talaris_contracts.bind import BindRequest, bind_message

    hotkey_hex = bytes(signing_key.verify_key).hex()
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        att = c.get("/attestation", params={"nonce": "00" * 32})
        att.raise_for_status()
        doc = att.json()
        message = bind_message(doc["boot_nonce"], doc["fingerprint"])
        signature = signing_key.sign(message).signature.hex()
        req = BindRequest(miner_pubkey=hotkey_hex, signature=signature)
        resp = c.post("/bind", json=req.model_dump())
        resp.raise_for_status()
        log(f"bound hotkey into {base_url}: miner_hash={resp.json().get('miner')}")
    return hotkey_hex


def register_miner(signing_key: SigningKey) -> dict:
    """Build a signed SelfRegistration with TWO HostedModels (per-model tee_endpoint)."""
    from talaris_contracts import HostedModel, sign_registration_ed25519

    reg = sign_registration_ed25519(
        signing_key=signing_key,
        peer_id="e2e-mm-peer-0001",
        subnet_node_id=11,
        tee_endpoint=TEE_A_BASE,  # Miner-row fallback; each model carries its own.
        hosted_models=[
            HostedModel(model_id=MODEL_A, tee_endpoint=TEE_A_BASE),
            HostedModel(model_id=MODEL_B, tee_endpoint=TEE_B_BASE),
        ],
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


def poll_models_attested(
    api_env: dict, hotkey: str, model_ids: list[str], timeout: float = 180.0
) -> dict:
    """Poll the DB until ALL model-enclaves are attested + have an active token + loaded."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        state = db_query(api_env, MINER_STATE_SNIPPET, hotkey)
        last = state
        if state.get("found") and state.get("health") == "healthy":
            by_id = {m["model_id"]: m for m in state["models"]}
            if all(
                mid in by_id
                and by_id[mid]["attestation_status"] == "attested"
                and by_id[mid]["has_active_token"]
                and by_id[mid]["loaded"]
                for mid in model_ids
            ):
                return state
        time.sleep(2.0)
    raise RuntimeError(
        f"models never all attested; last state={json.dumps(last)}"
    )


# --------------------------------------------------------------------------- #
# Developer inference (per-model, non-stream) with a client-supplied request_id.
# --------------------------------------------------------------------------- #
def developer_chat_completion(model_id: str, request_id: str) -> dict:
    with httpx.Client(base_url=API_BASE, timeout=30.0) as c:
        resp = c.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {SK_KEY}",
                "X-Request-ID": request_id,
            },
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": f"ping {model_id}"}],
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"chat/completions[{model_id}] failed {resp.status_code}: {resp.text}"
        )
    return resp.json()


# --------------------------------------------------------------------------- #
# Bound-miner /usage/receipts (WS-C / #9): sign receipts_auth_message, real HTTP.
# --------------------------------------------------------------------------- #
def fetch_receipts(base_url: str, signing_key: SigningKey, name: str) -> dict:
    """GET /usage/receipts authed as the bound miner (ed25519). Retries same-second replay."""
    from talaris_contracts import receipts_auth_message

    last_err = None
    for _attempt in range(6):
        ts = int(time.time())
        message = receipts_auth_message(ts, "/usage/receipts")
        sig = signing_key.sign(message).signature.hex()
        headers = {"X-Miner-Timestamp": str(ts), "X-Miner-Signature": sig}
        with httpx.Client(base_url=base_url, timeout=10.0) as c:
            resp = c.get("/usage/receipts", headers=headers)
        if resp.status_code == 200:
            return resp.json()
        last_err = f"{resp.status_code}: {resp.text}"
        body = resp.text.lower()
        # Same-second (timestamp,signature) reuse trips the per-enclave replay guard;
        # this is a harness timing artifact -> bump the timestamp and retry.
        if resp.status_code == 401 and (
            "repl" in body or "freshness" in body or "window" in body
        ):
            time.sleep(1.2)
            continue
        break
    raise RuntimeError(f"/usage/receipts[{name}] failed: {last_err}")


def fetch_receipts_unauthed(base_url: str) -> int:
    """GET /usage/receipts with NO auth headers and no bearer -> expect 401."""
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        resp = c.get("/usage/receipts")
    return resp.status_code


def _collect_keys(obj, acc: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _collect_keys(v, acc)
    elif isinstance(obj, list):
        for it in obj:
            _collect_keys(it, acc)


def receipts_usage_count(recv: dict) -> int:
    return int(recv.get("usage_count") or 0)


def receipts_request_ids(recv: dict) -> set:
    return {
        r.get("request_id")
        for r in (recv.get("rows") or [])
        if r.get("request_id")
    }


# --------------------------------------------------------------------------- #
# Allowlist artifact (WS-F).
# --------------------------------------------------------------------------- #
def fetch_and_verify_allowlist() -> tuple[bool, dict]:
    from talaris_contracts import ModelAllowlistArtifact, verify_model_allowlist

    with httpx.Client(base_url=API_BASE, timeout=10.0) as c:
        resp = c.get("/allowlist")
    resp.raise_for_status()
    payload = resp.json()
    artifact = ModelAllowlistArtifact(**payload)
    ok = verify_model_allowlist(artifact, PLATFORM_PROVISIONER_VERIFY_KEY_HEX)
    return ok, payload


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
# Teardown helpers (verbatim from run_e2e.py)
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


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    tee_a: Proc | None = None
    tee_b: Proc | None = None
    api_proc: Proc | None = None
    tmpdir = tempfile.mkdtemp(prefix="talaris_e2e_mm_")
    tmp = Path(tmpdir)
    logdir = tmp / "logs"
    logdir.mkdir()
    log(f"tmp dir: {tmp}")

    rc = 1
    try:
        # --- 1. start the four processes --- #
        start_redis()
        tee_a = start_tee(TEE_A_PORT, TEE_A_ENGINE_PORT, tmp / "usage_a.db", logdir, "a")
        tee_b = start_tee(TEE_B_PORT, TEE_B_ENGINE_PORT, tmp / "usage_b.db", logdir, "b")
        api_proc = start_api(tmp, logdir)
        api_env = api_proc.popen._e2e_env  # type: ignore[attr-defined]

        wait_http_ok(f"{TEE_A_BASE}/healthz", name="TEE-A /healthz")
        wait_http_ok(f"{TEE_B_BASE}/healthz", name="TEE-B /healthz")
        wait_http_ok(f"{API_BASE}/health", name="inference-api /health")

        # --- 2. seed the inference-api DB --- #
        seed_db(api_env)

        # --- 3. bind the SAME miner hotkey into BOTH enclaves --- #
        miner_signing_key = SigningKey.generate()
        hotkey = bind_hotkey_into_tee(miner_signing_key, TEE_A_BASE)
        hotkey_b = bind_hotkey_into_tee(miner_signing_key, TEE_B_BASE)
        check(hotkey == hotkey_b, "same miner hotkey bound into BOTH enclaves")

        # --- 4. register ONCE with two HostedModels (per-model tee_endpoint) --- #
        register_miner(miner_signing_key)

        # --- 5. wait for the provisioner to attest BOTH model-enclaves --- #
        log("polling DB until BOTH model-enclaves attested + provisioned + engines loaded...")
        state = poll_models_attested(api_env, hotkey, [MODEL_A, MODEL_B])
        log(f"miner DB state: {json.dumps(state)}")
        by_id = {m["model_id"]: m for m in state["models"]}
        check(state["health"] == "healthy", "miner health == healthy")
        for mid, expected_ep in ((MODEL_A, TEE_A_BASE), (MODEL_B, TEE_B_BASE)):
            mm = by_id[mid]
            check(mm["attestation_status"] == "attested",
                  f"MinerModel {mid!r} attestation_status == attested")
            check(mm["tee_endpoint"] == expected_ep,
                  f"MinerModel {mid!r} tee_endpoint == {expected_ep}")
            check(mm["loaded"], f"MinerModel {mid!r} loaded == True (engine started)")
            check(mm["has_active_token"],
                  f"MinerModel {mid!r} has an active per-model ProvisionedToken")
            check(mm["has_admin_token"],
                  f"MinerModel {mid!r} active token carries an encrypted admin token")
        # Distinct per-(miner,model) token ROWS (one active token row per model_id; the
        # sealing key_id is shared because both are sealed with the same platform key).
        check(by_id[MODEL_A]["token_id"] and by_id[MODEL_B]["token_id"]
              and by_id[MODEL_A]["token_id"] != by_id[MODEL_B]["token_id"],
              "per-model ProvisionedToken rows are distinct (one active token per model_id)")

        # --- 6. baseline per-enclave usage (fresh usage.db -> 0 each) --- #
        recv_a0 = fetch_receipts(TEE_A_BASE, miner_signing_key, "A baseline")
        recv_b0 = fetch_receipts(TEE_B_BASE, miner_signing_key, "B baseline")
        base_a = receipts_usage_count(recv_a0)
        base_b = receipts_usage_count(recv_b0)
        log(f"baseline usage_count: enclave A={base_a} enclave B={base_b}")

        # --- 7. infer mock-model-a (must route to enclave A) --- #
        rid_a = "e2e-a-" + uuid.uuid4().hex
        log(f"developer POST /v1/chat/completions model={MODEL_A} X-Request-ID={rid_a}...")
        comp_a = developer_chat_completion(MODEL_A, rid_a)
        content_a = comp_a["choices"][0]["message"]["content"]
        usage_a = comp_a.get("usage") or {}
        check(content_a == EXPECTED_CONTENT, f"{MODEL_A} content == {EXPECTED_CONTENT!r}")
        check(usage_a.get("prompt_tokens") == EXPECTED_PROMPT_TOKENS,
              f"{MODEL_A} usage.prompt_tokens == {EXPECTED_PROMPT_TOKENS}")
        check(usage_a.get("completion_tokens") == EXPECTED_COMPLETION_TOKENS,
              f"{MODEL_A} usage.completion_tokens == {EXPECTED_COMPLETION_TOKENS}")
        check(usage_a.get("total_tokens") == EXPECTED_TOTAL_TOKENS,
              f"{MODEL_A} usage.total_tokens == {EXPECTED_TOTAL_TOKENS}")

        recv_a1 = fetch_receipts(TEE_A_BASE, miner_signing_key, "A after model-a")
        recv_b1 = fetch_receipts(TEE_B_BASE, miner_signing_key, "B after model-a")
        cnt_a1 = receipts_usage_count(recv_a1)
        cnt_b1 = receipts_usage_count(recv_b1)
        log(f"after model-a: enclave A usage_count={cnt_a1} enclave B usage_count={cnt_b1}")
        check(cnt_a1 == base_a + 1, "enclave A usage_count incremented by 1 after model-a")
        check(cnt_b1 == base_b, "enclave B usage_count UNCHANGED after model-a (routing isolation)")
        check(rid_a in receipts_request_ids(recv_a1),
              f"enclave A receipts contain request_id {rid_a!r}")
        check(rid_a not in receipts_request_ids(recv_b1),
              "enclave B receipts do NOT contain model-a's request_id")

        # --- 8. infer mock-model-b (must route to enclave B) --- #
        rid_b = "e2e-b-" + uuid.uuid4().hex
        log(f"developer POST /v1/chat/completions model={MODEL_B} X-Request-ID={rid_b}...")
        comp_b = developer_chat_completion(MODEL_B, rid_b)
        content_b = comp_b["choices"][0]["message"]["content"]
        usage_b = comp_b.get("usage") or {}
        check(content_b == EXPECTED_CONTENT, f"{MODEL_B} content == {EXPECTED_CONTENT!r}")
        check(usage_b.get("total_tokens") == EXPECTED_TOTAL_TOKENS,
              f"{MODEL_B} usage.total_tokens == {EXPECTED_TOTAL_TOKENS}")

        recv_a2 = fetch_receipts(TEE_A_BASE, miner_signing_key, "A after model-b")
        recv_b2 = fetch_receipts(TEE_B_BASE, miner_signing_key, "B after model-b")
        cnt_a2 = receipts_usage_count(recv_a2)
        cnt_b2 = receipts_usage_count(recv_b2)
        log(f"after model-b: enclave A usage_count={cnt_a2} enclave B usage_count={cnt_b2}")
        check(cnt_a2 == cnt_a1, "enclave A usage_count UNCHANGED after model-b (routing isolation)")
        check(cnt_b2 == base_b + 1, "enclave B usage_count incremented by 1 after model-b")
        check(rid_b in receipts_request_ids(recv_b2),
              f"enclave B receipts contain request_id {rid_b!r}")
        check(rid_b not in receipts_request_ids(recv_a2),
              "enclave A receipts do NOT contain model-b's request_id")

        # Final per-enclave totals == number of requests routed to each.
        check(cnt_a2 == base_a + 1, "enclave A served exactly 1 request (mock-model-a)")
        check(cnt_b2 == base_b + 1, "enclave B served exactly 1 request (mock-model-b)")

        # --- 9. receipts are METADATA-ONLY + head_signature/verify_key present --- #
        for recv, label in ((recv_a2, "A"), (recv_b2, "B")):
            keys: set = set()
            _collect_keys(recv, keys)
            leaked = FORBIDDEN_RECEIPT_KEYS & keys
            check(not leaked,
                  f"enclave {label} receipts are metadata-only (no {sorted(FORBIDDEN_RECEIPT_KEYS)} keys; leaked={sorted(leaked)})")
            check(bool(recv.get("head_signature")),
                  f"enclave {label} receipts head_signature present and non-empty")
            check(bool(recv.get("verify_key")),
                  f"enclave {label} receipts verify_key present and non-empty")

        # --- 10. NEGATIVE: no auth headers -> 401 --- #
        for base, label in ((TEE_A_BASE, "A"), (TEE_B_BASE, "B")):
            sc = fetch_receipts_unauthed(base)
            check(sc == 401, f"enclave {label} /usage/receipts with NO auth -> 401 (got {sc})")

        # --- 11. billing + request_id cross-check (API <-> TEE) --- #
        deadline = time.time() + 25
        billing = {}
        while time.time() < deadline:
            billing = db_query(api_env, BILLING_SNIPPET)
            settled = [e for e in billing["events"] if e["status"] == "settled"]
            if len(settled) >= 2:
                break
            time.sleep(1.0)
        settled = [e for e in billing["events"] if e["status"] == "settled"]
        check(len(settled) >= 2, "two settled InferenceUsageEvents exist (one per model)")
        ev_by_model = {e["model"]: e for e in settled}
        check(MODEL_A in ev_by_model and MODEL_B in ev_by_model,
              "settled events cover BOTH models")
        ev_a, ev_b = ev_by_model[MODEL_A], ev_by_model[MODEL_B]

        # request_id cross-check: API event request_id == client X-Request-ID == TEE row request_id.
        check(ev_a["request_id"] == rid_a,
              f"API InferenceUsageEvent[{MODEL_A}].request_id == {rid_a}")
        check(ev_b["request_id"] == rid_b,
              f"API InferenceUsageEvent[{MODEL_B}].request_id == {rid_b}")
        check(ev_a["request_id"] in receipts_request_ids(recv_a2),
              "model-a API event request_id MATCHES an enclave-A receipt request_id")
        check(ev_b["request_id"] in receipts_request_ids(recv_b2),
              "model-b API event request_id MATCHES an enclave-B receipt request_id")

        for ev, mid in ((ev_a, MODEL_A), (ev_b, MODEL_B)):
            check(ev["miner_hotkey"] == hotkey, f"{mid} settled event miner_hotkey == registered hotkey")
            check(ev["miner_id"] is not None, f"{mid} settled event has miner_id set")
            check(ev["prompt_tokens"] == EXPECTED_PROMPT_TOKENS, f"{mid} settled prompt_tokens == {EXPECTED_PROMPT_TOKENS}")
            check(ev["completion_tokens"] == EXPECTED_COMPLETION_TOKENS, f"{mid} settled completion_tokens == {EXPECTED_COMPLETION_TOKENS}")
            check(ev["raw_total_tokens"] == EXPECTED_TOTAL_TOKENS, f"{mid} settled raw_total_tokens == {EXPECTED_TOTAL_TOKENS}")
            check(ev["charged_tokens"] == EXPECTED_TOTAL_TOKENS, f"{mid} charged_tokens == {EXPECTED_TOTAL_TOKENS} (multiplier 1.0)")

        period = billing["periods"][0]
        log(f"usage period: used={period['used_tokens']} remaining={period['remaining_tokens']} "
            f"allowance={period['total_allowance']}")
        check(period["used_tokens"] == 2 * EXPECTED_TOTAL_TOKENS,
              f"period used_tokens == {2 * EXPECTED_TOTAL_TOKENS} (both models billed)")
        check(period["remaining_tokens"] == period["total_allowance"] - 2 * EXPECTED_TOTAL_TOKENS,
              "remaining credits decreased by both charges")

        # --- 12. allowlist artifact (WS-F) --- #
        allow_ok, allow_payload = fetch_and_verify_allowlist()
        check(allow_ok,
              "GET /allowlist artifact verifies under PLATFORM_PROVISIONER_VERIFY_KEY_HEX (verify_model_allowlist)")

        # --- 13. report PASS --- #
        print("", flush=True)
        print("=" * 72, flush=True)
        print("E2E PASS  (multi-model serving + receipt/auth pipeline)", flush=True)
        print("=" * 72, flush=True)
        print(f"  models served        : {MODEL_A} @ {TEE_A_BASE}  |  {MODEL_B} @ {TEE_B_BASE}", flush=True)
        print(f"  both attested        : {by_id[MODEL_A]['attestation_status']} / {by_id[MODEL_B]['attestation_status']} "
              f"(mode={by_id[MODEL_A]['attestation_mode']})", flush=True)
        print(f"  per-model tokens     : A.token={by_id[MODEL_A]['token_id']}  B.token={by_id[MODEL_B]['token_id']} "
              f"(distinct rows; sealing key_id={by_id[MODEL_A]['token_key_id']})", flush=True)
        print(f"  enclave A usage_count: {cnt_a2}  (served {MODEL_A})", flush=True)
        print(f"  enclave B usage_count: {cnt_b2}  (served {MODEL_B})", flush=True)
        print(f"  routing isolation    : model-a -> A only, model-b -> B only (cross counts unchanged)", flush=True)
        print(f"  request_id cross-chk : {MODEL_A}: {rid_a} (API==TEE)", flush=True)
        print(f"                         {MODEL_B}: {rid_b} (API==TEE)", flush=True)
        print(f"  receipts             : metadata-only, head_signature+verify_key present, no-auth->401", flush=True)
        print(f"  billing              : used={period['used_tokens']} remaining={period['remaining_tokens']} "
              f"(2 x {EXPECTED_TOTAL_TOKENS})", flush=True)
        print(f"  allowlist verdict    : verify_model_allowlist == {allow_ok} "
              f"(entries={len(allow_payload.get('entries', []))}, version={allow_payload.get('version')})", flush=True)
        print(f"  serving miner hotkey : {hotkey}", flush=True)
        print("=" * 72, flush=True)
        rc = 0
    except Exception as exc:  # noqa: BLE001
        log(f"E2E FAIL: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        dump_log(logdir, "tee_a.log")
        dump_log(logdir, "tee_b.log")
        dump_log(logdir, "api.log")
        rc = 1
    finally:
        log("tearing down...")
        terminate(api_proc)
        terminate(tee_a)
        terminate(tee_b)
        stop_redis()
        log(f"logs preserved under {logdir}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
