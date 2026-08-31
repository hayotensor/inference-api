"""NVIDIA local backend: guarded-SDK fail-closed + the shared nonce-binding contract.

The real ``nv_attestation_sdk`` is NOT installed in this repo, so the primary assertion is
that the uninstalled-SDK path fails CLOSED with a clear reason naming the missing extra.

To prove the verification CONTRACT (identical to NRAS: nonce binding + overall-result +
UUID extraction) without the SDK, we inject a tiny FAKE SDK client via
``sdk_client_factory`` that mimics the documented Attestation API (set_nonce / attest /
get_token) and returns a compact-JWS token. This is offline and deterministic.
"""

from __future__ import annotations

import base64
import json
import time

from talaris_attest.backends.nvidia_local import NvidiaLocalBackend

ISSUER = "https://nras.test.local"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unsigned_compact_jws(claims: dict) -> str:
    header = _b64url(json.dumps({"alg": "ES384", "kid": "local"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    return f"{header}.{payload}.{_b64url(b'local-sig')}"


def _good_claims(nonce_hex: str, **overrides) -> dict:
    claims = {
        "eat_nonce": nonce_hex,
        "iss": ISSUER,
        "exp": int(time.time()) + 3600,
        "x-nvidia-overall-att-result": "success",
        "submods": {
            "GPU-0": {"x-nvidia-gpu-uuid": "GPU-uuid-1"},
            "GPU-1": {"x-nvidia-gpu-uuid": "GPU-uuid-2"},
        },
    }
    claims.update(overrides)
    return claims


class _FakeSdkClient:
    """Mimics the nv_attestation_sdk Attestation client surface we use."""

    def __init__(self, claims_for_nonce, attest_ok=True, policy_ok=True):
        self._claims_for_nonce = claims_for_nonce
        self._attest_ok = attest_ok
        self._policy_ok = policy_ok
        self._nonce = None

    def set_name(self, name):  # pragma: no cover - trivial
        pass

    def set_nonce(self, nonce):
        self._nonce = nonce

    def add_verifier(self, *args, **kwargs):  # pragma: no cover - trivial
        pass

    def attest(self, evidence=None):
        return self._attest_ok

    def get_token(self):
        # The fake echoes whatever nonce the caller bound, modeling NVIDIA's echo.
        return _unsigned_compact_jws(self._claims_for_nonce(self._nonce))

    def validate_token(self, policy, token):
        return self._policy_ok


def _factory(**kwargs):
    return lambda: _FakeSdkClient(**kwargs)


# --------------------------------------------------------------------------- #
# Primary: uninstalled SDK fails CLOSED
# --------------------------------------------------------------------------- #
def test_local_sdk_uninstalled_fails_closed():
    """With nv_attestation_sdk absent (this repo), verify() fails closed clearly."""
    backend = NvidiaLocalBackend()  # no injected factory -> tries the real import
    res = backend.verify({"evidences": ["blob"]}, "ab" * 32)
    assert res.verified is False
    assert "nv_attestation_sdk" in (res.reason or "")
    assert "nvidia-local" in (res.reason or "")


def test_local_is_secure():
    assert NvidiaLocalBackend.is_secure is True


# --------------------------------------------------------------------------- #
# Contract parity with NRAS, proven via an injected fake SDK client (offline)
# --------------------------------------------------------------------------- #
def test_local_contract_success_with_fake_sdk():
    nonce_hex = "ab" * 32
    backend = NvidiaLocalBackend(
        sdk_client_factory=_factory(claims_for_nonce=lambda n: _good_claims(n))
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True, res.reason
    assert res.measured_gpu_uuids == ("GPU-uuid-1", "GPU-uuid-2")


def test_local_nonce_mismatch_rejected():
    """If the token echoes a different nonce than bound, fail closed (binding enforced)."""
    nonce_hex = "ab" * 32
    backend = NvidiaLocalBackend(
        # token always echoes a fixed wrong nonce regardless of what was bound
        sdk_client_factory=_factory(claims_for_nonce=lambda n: _good_claims("cd" * 32))
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "nonce" in (res.reason or "").lower()


def test_local_attest_false_rejected():
    nonce_hex = "ab" * 32
    backend = NvidiaLocalBackend(
        sdk_client_factory=_factory(
            claims_for_nonce=lambda n: _good_claims(n), attest_ok=False
        )
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "not-ok" in (res.reason or "").lower()


def test_local_overall_failure_rejected():
    nonce_hex = "ab" * 32
    backend = NvidiaLocalBackend(
        sdk_client_factory=_factory(
            claims_for_nonce=lambda n: _good_claims(
                n, **{"x-nvidia-overall-att-result": "fail"}
            )
        )
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "result" in (res.reason or "").lower()


def test_local_policy_failure_rejected():
    nonce_hex = "ab" * 32
    backend = NvidiaLocalBackend(
        policy={"version": "1.0"},
        sdk_client_factory=_factory(
            claims_for_nonce=lambda n: _good_claims(n), policy_ok=False
        ),
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "policy" in (res.reason or "").lower()
