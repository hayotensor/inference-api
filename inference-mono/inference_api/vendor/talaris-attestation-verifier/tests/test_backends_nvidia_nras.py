"""NVIDIA NRAS backend: REAL EAT (compact JWS) signature verification, offline KAT.

These tests generate TEST signing keys (EC P-384 / P-256 and RSA), build a valid compact
JWS EAT signed by them, and serve a JWKS containing the public key via an INJECTED
``jwks_transport`` (no network). The default path (``trust_eat_signature=False``) does REAL
verification:

  * happy path (ES384 + RS256): signature verifies, claims (iss/exp) pass, nonce binds ->
    verified=True with the measured GPU UUIDs;
  * negative controls each fail CLOSED: tampered signature, wrong kid (not in JWKS),
    alg=none, expired exp, wrong iss, nonce mismatch, overall-result != success;
  * with NO jwks_url configured, the default path fails CLOSED (cannot establish trust).

The legacy ``trust_eat_signature=True`` test bypass is still covered to show it exercises
the nonce-binding logic without the crypto check.
"""

from __future__ import annotations

import base64
import datetime
import json
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID

from talaris_attest.backends.nvidia_nras import NvidiaNrasBackend

ISSUER = "https://nras.test.local"


# --------------------------------------------------------------------------- #
# JWS / JWKS test helpers (build genuinely-signed compact JWS EATs)
# --------------------------------------------------------------------------- #
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_uint(n: int) -> str:
    length = (n.bit_length() + 7) // 8
    return _b64url(n.to_bytes(length, "big"))


def _ec_jwk(public_key, kid: str, crv: str) -> dict:
    nums = public_key.public_numbers()
    coord_len = (public_key.curve.key_size + 7) // 8
    return {
        "kty": "EC",
        "crv": crv,
        "x": _b64url(nums.x.to_bytes(coord_len, "big")),
        "y": _b64url(nums.y.to_bytes(coord_len, "big")),
        "kid": kid,
    }


def _rsa_jwk(public_key, kid: str) -> dict:
    nums = public_key.public_numbers()
    return {
        "kty": "RSA",
        "n": _b64url_uint(nums.n),
        "e": _b64url_uint(nums.e),
        "kid": kid,
    }


def _sign_es(private_key, alg: str, signing_input: bytes) -> bytes:
    hash_cls = {"ES256": hashes.SHA256, "ES384": hashes.SHA384}[alg]
    der = private_key.sign(signing_input, ec.ECDSA(hash_cls()))
    r, s = decode_dss_signature(der)
    coord_len = (private_key.curve.key_size + 7) // 8
    return r.to_bytes(coord_len, "big") + s.to_bytes(coord_len, "big")


def _sign_rs256(private_key, signing_input: bytes) -> bytes:
    return private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())


def _make_signed_eat(private_key, alg: str, kid: str, claims: dict) -> str:
    header = _b64url(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    if alg.startswith("ES"):
        sig = _sign_es(private_key, alg, signing_input)
    elif alg == "RS256":
        sig = _sign_rs256(private_key, signing_input)
    else:
        raise ValueError(alg)
    return f"{header}.{payload}.{_b64url(sig)}"


def _jwks_transport_for(*jwks: dict):
    def transport(url):
        return {"keys": list(jwks)}

    return transport


def _transport_returning(eat: str):
    captured = {}

    def transport(url, body):
        captured["url"] = url
        captured["body"] = body
        return {"eat": eat}

    transport.captured = captured
    return transport


def _good_claims(nonce_hex: str, **overrides) -> dict:
    claims = {
        "eat_nonce": nonce_hex,
        "iss": ISSUER,
        "iat": int(time.time()) - 10,
        "exp": int(time.time()) + 3600,
        "x-nvidia-overall-att-result": "success",
        "submods": {
            "GPU-0": {"x-nvidia-gpu-uuid": "GPU-uuid-1"},
            "GPU-1": {"x-nvidia-gpu-uuid": "GPU-uuid-2"},
        },
    }
    claims.update(overrides)
    return claims


# --------------------------------------------------------------------------- #
# Fixtures-ish key builders
# --------------------------------------------------------------------------- #
def _make_es384():
    key = ec.generate_private_key(ec.SECP384R1())
    return key, _ec_jwk(key.public_key(), "test-es384", "P-384")


def _make_es256():
    key = ec.generate_private_key(ec.SECP256R1())
    return key, _ec_jwk(key.public_key(), "test-es256", "P-256")


def _make_rsa():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, _rsa_jwk(key.public_key(), "test-rs256")


def _backend(jwks, **kwargs):
    """Build a backend whose transport returns the given EAT and whose JWKS is injected."""
    eat = kwargs.pop("eat")
    return NvidiaNrasBackend(
        transport=_transport_returning(eat),
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=_jwks_transport_for(*jwks),
        expected_issuer=ISSUER,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# REAL verification — happy paths
# --------------------------------------------------------------------------- #
def test_nras_real_es384_success():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    eat = _make_signed_eat(key, "ES384", "test-es384", _good_claims(nonce_hex))
    backend = _backend([jwk], eat=eat)  # trust_eat_signature defaults to False (REAL)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True, res.reason
    assert res.measured_gpu_uuids == ("GPU-uuid-1", "GPU-uuid-2")
    assert "ES384" in (res.reason or "")


def test_nras_real_es256_success():
    nonce_hex = "cd" * 32
    key, jwk = _make_es256()
    eat = _make_signed_eat(key, "ES256", "test-es256", _good_claims(nonce_hex))
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True, res.reason


def test_nras_real_rs256_success():
    nonce_hex = "ef" * 32
    key, jwk = _make_rsa()
    eat = _make_signed_eat(key, "RS256", "test-rs256", _good_claims(nonce_hex))
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True, res.reason
    assert res.measured_gpu_uuids == ("GPU-uuid-1", "GPU-uuid-2")


def test_nras_selects_correct_key_among_many():
    """JWKS with several keys; the one matching kid is used."""
    nonce_hex = "ab" * 32
    es384_key, es384_jwk = _make_es384()
    _, es256_jwk = _make_es256()
    _, rsa_jwk = _make_rsa()
    eat = _make_signed_eat(es384_key, "ES384", "test-es384", _good_claims(nonce_hex))
    backend = _backend([es256_jwk, rsa_jwk, es384_jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True, res.reason


# --------------------------------------------------------------------------- #
# Negative controls — each must fail CLOSED
# --------------------------------------------------------------------------- #
def test_nras_tampered_signature_rejected():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    eat = _make_signed_eat(key, "ES384", "test-es384", _good_claims(nonce_hex))
    head, payload, sig = eat.split(".")
    # Flip a byte in the signature segment.
    bad_sig = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    tampered = f"{head}.{payload}.{bad_sig}"
    backend = _backend([jwk], eat=tampered)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "signature" in (res.reason or "").lower()


def test_nras_tampered_payload_rejected():
    """Re-signing-input changes when the payload is altered -> signature no longer valid."""
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    eat = _make_signed_eat(key, "ES384", "test-es384", _good_claims(nonce_hex))
    head, _payload, sig = eat.split(".")
    forged_payload = base64.urlsafe_b64encode(
        json.dumps(_good_claims("99" * 32)).encode()
    ).rstrip(b"=").decode()
    tampered = f"{head}.{forged_payload}.{sig}"
    backend = _backend([jwk], eat=tampered)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False


def test_nras_wrong_kid_rejected():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    # Header uses kid 'unknown' but the JWKS only carries 'test-es384'.
    eat = _make_signed_eat(key, "ES384", "unknown-kid", _good_claims(nonce_hex))
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "kid" in (res.reason or "").lower()


def test_nras_alg_none_rejected():
    nonce_hex = "ab" * 32
    _key, jwk = _make_es384()
    header = _b64url(json.dumps({"alg": "none", "kid": "test-es384"}).encode())
    payload = _b64url(json.dumps(_good_claims(nonce_hex)).encode())
    eat = f"{header}.{payload}."  # empty signature
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "none" in (res.reason or "").lower()


def test_nras_disallowed_alg_rejected():
    """An otherwise-supported alg that isn't in allowed_algs is rejected (alg confusion)."""
    nonce_hex = "ab" * 32
    key, jwk = _make_es256()
    eat = _make_signed_eat(key, "ES256", "test-es256", _good_claims(nonce_hex))
    backend = _backend([jwk], eat=eat, allowed_algs=("ES384", "RS256"))
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "alg" in (res.reason or "").lower()


def test_nras_expired_exp_rejected():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    claims = _good_claims(nonce_hex, exp=int(time.time()) - 3600, iat=int(time.time()) - 7200)
    eat = _make_signed_eat(key, "ES384", "test-es384", claims)
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "expired" in (res.reason or "").lower()


def test_nras_wrong_iss_rejected():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    claims = _good_claims(nonce_hex, iss="https://evil.example.com")
    eat = _make_signed_eat(key, "ES384", "test-es384", claims)
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "iss" in (res.reason or "").lower()


def test_nras_nonce_mismatch_rejected():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    claims = _good_claims("cd" * 32)  # echoes a different nonce
    eat = _make_signed_eat(key, "ES384", "test-es384", claims)
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "nonce" in (res.reason or "").lower()


def test_nras_overall_failure_rejected():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    claims = _good_claims(nonce_hex)
    claims["x-nvidia-overall-att-result"] = "fail"
    eat = _make_signed_eat(key, "ES384", "test-es384", claims)
    backend = _backend([jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "result" in (res.reason or "").lower()


def test_nras_signed_with_other_key_rejected():
    """EAT signed by a different private key than the one published in the JWKS."""
    nonce_hex = "ab" * 32
    other_key, _ = _make_es384()
    _, published_jwk = _make_es384()
    published_jwk["kid"] = "test-es384"
    eat = _make_signed_eat(other_key, "ES384", "test-es384", _good_claims(nonce_hex))
    backend = _backend([published_jwk], eat=eat)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "signature" in (res.reason or "").lower()


# --------------------------------------------------------------------------- #
# Trust-anchor / config fail-closed behavior
# --------------------------------------------------------------------------- #
def test_nras_no_jwks_url_fails_closed():
    """With jwks_url explicitly None, trust cannot be established -> fail CLOSED
    (no network call)."""
    nonce_hex = "ab" * 32
    key, _ = _make_es384()
    eat = _make_signed_eat(key, "ES384", "test-es384", _good_claims(nonce_hex))
    backend = NvidiaNrasBackend(transport=_transport_returning(eat), jwks_url=None)
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "jwks" in (res.reason or "").lower()


def test_default_constructor_uses_real_jwks_and_is_secure():
    """NvidiaNrasBackend() with no args (production _default_gpu_backend) ships the real
    NVIDIA JWKS URL, is secure, and never trusts an EAT signature blindly."""
    from talaris_attest.backends.nvidia_nras import DEFAULT_NRAS_JWKS_URL

    backend = NvidiaNrasBackend()
    assert backend.is_secure is True
    assert backend.trust_eat_signature is False
    assert backend.jwks_url == DEFAULT_NRAS_JWKS_URL
    assert backend.jwks_url and backend.jwks_url.startswith("https://nras.attestation.nvidia.com")


def test_unparseable_eat_fails_closed_without_network():
    """A malformed EAT fails closed before any JWKS fetch (jwks_url=None => no network)."""
    backend = NvidiaNrasBackend(
        transport=lambda url, body: {"eat": "a.b.c"}, jwks_url=None
    )
    res = backend.verify({"evidences": ["blob"]}, "ab" * 32)
    assert res.verified is False


def test_jwks_transport_is_cached():
    """The JWKS transport is called once across multiple verifications (TTL cache)."""
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    eat = _make_signed_eat(key, "ES384", "test-es384", _good_claims(nonce_hex))

    calls = {"n": 0}

    def counting_transport(url):
        calls["n"] += 1
        return {"keys": [jwk]}

    backend = NvidiaNrasBackend(
        transport=_transport_returning(eat),
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=counting_transport,
        expected_issuer=ISSUER,
    )
    for _ in range(3):
        assert backend.verify({"evidences": ["blob"]}, nonce_hex).verified is True
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# Legacy test-only bypass still exercises nonce binding without crypto
# --------------------------------------------------------------------------- #
def test_nras_trust_bypass_nonce_binding():
    nonce_hex = "ab" * 32
    header = _b64url(json.dumps({"alg": "ES384", "kid": "test"}).encode())
    payload = _b64url(json.dumps(_good_claims(nonce_hex)).encode())
    eat = f"{header}.{payload}.{_b64url(b'not-a-real-signature')}"
    backend = NvidiaNrasBackend(
        transport=_transport_returning(eat), trust_eat_signature=True
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True
    assert "TEST-ONLY" in (res.reason or "")


def test_nras_trust_bypass_nonce_mismatch_still_rejected():
    nonce_hex = "ab" * 32
    header = _b64url(json.dumps({"alg": "ES384", "kid": "test"}).encode())
    payload = _b64url(json.dumps(_good_claims("cd" * 32)).encode())
    eat = f"{header}.{payload}.{_b64url(b'sig')}"
    backend = NvidiaNrasBackend(
        transport=_transport_returning(eat), trust_eat_signature=True
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "nonce" in (res.reason or "")


def test_nras_request_carries_nonce():
    nonce_hex = "ab" * 32
    key, jwk = _make_es384()
    eat = _make_signed_eat(key, "ES384", "test-es384", _good_claims(nonce_hex))
    transport = _transport_returning(eat)
    backend = NvidiaNrasBackend(
        transport=transport,
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=_jwks_transport_for(jwk),
        expected_issuer=ISSUER,
    )
    backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert transport.captured["body"]["nonce"] == nonce_hex


# --------------------------------------------------------------------------- #
# x5c chain verification to a pinned (test) root
# --------------------------------------------------------------------------- #
def _self_signed_root():
    key = ec.generate_private_key(ec.SECP384R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test NVIDIA Root")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA384())
    )
    return key, cert


def _leaf_signed_by(root_key, root_cert, leaf_pub):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NRAS EAT Signer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(root_cert.subject)
        .public_key(leaf_pub)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(root_key, hashes.SHA384())
    )
    return cert


def test_nras_x5c_chain_verified_to_pinned_root():
    """When the JWK carries an x5c chaining to the pinned root, verification passes."""
    nonce_hex = "ab" * 32
    root_key, root_cert = _self_signed_root()
    leaf_key = ec.generate_private_key(ec.SECP384R1())
    leaf_cert = _leaf_signed_by(root_key, root_cert, leaf_key.public_key())
    jwk = _ec_jwk(leaf_key.public_key(), "test-es384", "P-384")
    jwk["x5c"] = [base64.b64encode(
        leaf_cert.public_bytes(serialization.Encoding.DER)
    ).decode()]
    root_pem = root_cert.public_bytes(serialization.Encoding.PEM).decode()
    eat = _make_signed_eat(leaf_key, "ES384", "test-es384", _good_claims(nonce_hex))
    backend = NvidiaNrasBackend(
        transport=_transport_returning(eat),
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=_jwks_transport_for(jwk),
        expected_issuer=ISSUER,
        nvidia_root_pem=root_pem,
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is True, res.reason
    assert "chain verified" in (res.reason or "").lower()


def test_nras_x5c_chain_to_wrong_root_rejected():
    """x5c that does NOT chain to the pinned root fails closed."""
    nonce_hex = "ab" * 32
    root_key, root_cert = _self_signed_root()
    other_root_key, _other_root_cert = _self_signed_root()  # different pinned root
    leaf_key = ec.generate_private_key(ec.SECP384R1())
    leaf_cert = _leaf_signed_by(root_key, root_cert, leaf_key.public_key())
    jwk = _ec_jwk(leaf_key.public_key(), "test-es384", "P-384")
    jwk["x5c"] = [base64.b64encode(
        leaf_cert.public_bytes(serialization.Encoding.DER)
    ).decode()]
    # Pin a DIFFERENT root than the one that signed the leaf.
    _, wrong_root_cert = _self_signed_root()
    wrong_root_pem = wrong_root_cert.public_bytes(serialization.Encoding.PEM).decode()
    eat = _make_signed_eat(leaf_key, "ES384", "test-es384", _good_claims(nonce_hex))
    backend = NvidiaNrasBackend(
        transport=_transport_returning(eat),
        jwks_url="https://jwks.test.local/keys",
        jwks_transport=_jwks_transport_for(jwk),
        expected_issuer=ISSUER,
        nvidia_root_pem=wrong_root_pem,
    )
    res = backend.verify({"evidences": ["blob"]}, nonce_hex)
    assert res.verified is False
    assert "x5c" in (res.reason or "").lower()


def test_nras_is_secure():
    assert NvidiaNrasBackend.is_secure is True
