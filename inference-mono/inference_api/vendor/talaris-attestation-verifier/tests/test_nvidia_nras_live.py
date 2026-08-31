"""LIVE validation of the NVIDIA NRAS wiring against the real service.

We cannot produce a genuine NVIDIA-signed EAT here (that needs real GPU evidence + a
published RIM), so we validate everything UP TO the signature key: the production JWKS
URL/endpoint defaults are the real NVIDIA values, the live JWKS is well-formed (ES384 / EC
P-384 with x5c), and — the key check — our ``_public_key_from_jwk`` reconstruction matches
the ``x5c[0]`` leaf cert's public key for a REAL production key. That proves the wiring our
production path relies on works against genuine NVIDIA data.

Marked ``live``; skipped cleanly when NRAS is unreachable (offline CI).
"""
from __future__ import annotations

import base64

import pytest

httpx = pytest.importorskip("httpx")
from cryptography.x509 import load_der_x509_certificate  # noqa: E402

from talaris_attest.backends.nvidia_nras import (  # noqa: E402
    DEFAULT_NRAS_JWKS_URL,
    DEFAULT_NRAS_URL,
    _httpx_jwks_transport,
    _public_key_from_jwk,
)

NRAS_JWKS = "https://nras.attestation.nvidia.com/.well-known/jwks.json"


def _nras_reachable() -> bool:
    try:
        r = httpx.get(NRAS_JWKS, timeout=10)
        return r.status_code == 200 and isinstance(r.json().get("keys"), list)
    except Exception:
        return False


live = pytest.mark.skipif(not _nras_reachable(), reason="NRAS JWKS not reachable")


def test_production_defaults_are_the_real_nvidia_values():
    assert DEFAULT_NRAS_JWKS_URL == NRAS_JWKS
    assert DEFAULT_NRAS_URL == "https://nras.attestation.nvidia.com/v3/attest/gpu"


@live
def test_live_jwks_is_wellformed_es384_ec():
    jwks = _httpx_jwks_transport(DEFAULT_NRAS_JWKS_URL)
    keys = jwks["keys"]
    assert keys, "live NRAS JWKS has no keys"
    # NRAS signs EATs with EC P-384 (ES384) keys carrying an x5c chain.
    assert all(k.get("kty") == "EC" and k.get("crv") == "P-384" for k in keys)
    assert all("kid" in k for k in keys)
    assert any("x5c" in k for k in keys)


@live
def test_live_jwk_reconstruction_matches_x5c_leaf_pubkey():
    """Our JWK->public-key reconstruction must equal the x5c[0] leaf cert's public key for a
    genuine NVIDIA production key — the exact key used to verify a real EAT signature."""
    jwks = _httpx_jwks_transport(DEFAULT_NRAS_JWKS_URL)
    jwk = next(k for k in jwks["keys"] if k.get("x5c"))

    reconstructed = _public_key_from_jwk(jwk, "ES384")
    leaf = load_der_x509_certificate(base64.b64decode(jwk["x5c"][0]))

    # Same EC public point => same key our backend would verify the EAT signature with.
    assert (
        reconstructed.public_numbers() == leaf.public_key().public_numbers()
    ), "JWK (x,y) reconstruction does not match the x5c[0] leaf certificate's public key"
    # And the x5c leaf is genuinely NVIDIA's attestation signing cert.
    assert "NVIDIA" in leaf.subject.rfc4514_string()
    assert "Attestation Service" in leaf.subject.rfc4514_string()
