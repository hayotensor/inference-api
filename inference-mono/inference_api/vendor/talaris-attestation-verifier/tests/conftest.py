"""Test fixtures: REAL dev-mode attestation documents from the producer, in-process.

We do not mock the producer. We run ``tee_wrapper`` in-process via a FastAPI TestClient,
hit ``GET /attestation?nonce=...``, and verify the genuine document the producer emits.
This is the only way to be sure the verifier's report_data / TLS-binding re-implementation
stays byte-exact with the source of truth.

Notes:
  * The producer refuses to boot the 'dev' attester on confidential-computing hardware
    (this box has /dev/tdx_guest), so we set allow_dev_mode=True for the test enclave.
  * TLS cert/key paths are redirected into a tmp dir (the default /run/tee may be
    read-only); generate_enclave_cert always writes a FRESH enclave-bound cert there.
  * With no engine started, model_hash/args_hash/gpu_hash come back None (the
    "engine not ready" case the verifier must handle); usage_count/total are 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the producer importable without requiring the caller to set PYTHONPATH.
_PRODUCER_SRC = Path(
    "/home/rizzo/talaris-inference/inference-subnet-miner/src"
)
if _PRODUCER_SRC.is_dir() and str(_PRODUCER_SRC) not in sys.path:
    sys.path.insert(0, str(_PRODUCER_SRC))


def _producer_available() -> bool:
    try:
        import tee_wrapper.app  # noqa: F401
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
        return True
    except Exception:
        return False


requires_producer = pytest.mark.skipif(
    not _producer_available(),
    reason="tee_wrapper producer / fastapi not importable",
)


def make_dev_doc(nonce_hex: str, tmp_path: Path) -> dict:
    """Run the producer in-process and return a genuine dev-mode attestation document."""
    from fastapi.testclient import TestClient

    from tee_wrapper.app import create_app
    from tee_wrapper.config import Settings

    settings = Settings(
        dev_mode=True,
        attestation_mode="dev",
        allow_dev_mode=True,  # this box has /dev/tdx_guest; allow the dev attester
        usage_db_path=":memory:",
        tls_cert_path=str(tmp_path / "cert.pem"),
        tls_key_path=str(tmp_path / "key.pem"),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        resp = client.get("/attestation", params={"nonce": nonce_hex})
        resp.raise_for_status()
        return resp.json()


@pytest.fixture
def dev_nonce() -> bytes:
    return bytes.fromhex("aa" * 32)


@pytest.fixture
def dev_doc(dev_nonce: bytes, tmp_path: Path) -> dict:
    """A genuine dev-mode attestation document for ``dev_nonce``."""
    if not _producer_available():
        pytest.skip("producer not importable")
    return make_dev_doc(dev_nonce.hex(), tmp_path)
