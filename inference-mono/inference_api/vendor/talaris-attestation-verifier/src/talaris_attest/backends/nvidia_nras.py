"""NVIDIA NRAS (Remote Attestation Service) backend — EAT JWS verification is REAL.

NRAS is NVIDIA's hosted remote-attestation REST service. The miner-side producer collects
raw GPU evidence with ``nvattest collect-evidence --nonce <report_data_hex>``; a verifier
submits that evidence to NRAS, which validates the NVIDIA signing chain (GPU certs ->
NVIDIA Device Identity CA / RIM) and returns an EAT (Entity Attestation Token, a signed
JWT/JWS) whose claims include the measurements and the ECHOED nonce.

What is REAL here (and unit-testable with a mocked transport):
  * the request structure (POST evidence to the NRAS endpoint), via an injectable
    ``transport`` callable so tests need no network;
  * EAT claim extraction + the nonce-binding check: the EAT's nonce claim MUST equal the
    enclave report_data hex (== the nonce we asked nvattest to sign over). A token whose
    nonce does not bind THIS request is rejected. Measured GPU UUIDs are pulled from the
    per-GPU claims.
  * **EAT JWS signature verification** against NRAS's published JWKS. The compact JWS is
    parsed, the signing key selected by `kid` from a (cached) JWKS, and the ES384/ES256/
    RS256 signature is verified directly with ``cryptography`` over ``header.payload``.
    Standard claims (``exp``/``iat``/``nbf``, plus ``iss`` when an expected issuer is
    configured) are validated. Optionally — when a root is pinned — the JWK's ``x5c``
    chain is also verified to it (defense-in-depth).

Trust model (matches NVIDIA's own ``nv-attestation-sdk`` ``nras_utils.decode_nras_token``):
  the JWKS is fetched over HTTPS from the pinned NRAS host — the TLS-authenticated JWKS
  endpoint IS the trust anchor — the signing key is selected by ``kid``, and the ES384
  signature is verified with that key. NVIDIA's SDK pins no root CA and does not enforce
  ``iss``; we keep both as OPTIONAL opt-ins (``nvidia_root_pem`` / ``expected_issuer``) so an
  operator can harden further without risking a fail-close on an unverified value. The
  production JWKS URL + endpoint defaults below are the real NVIDIA values (confirmed live).
"""

from __future__ import annotations

import base64
import json
import time
from typing import Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.x509 import load_der_x509_certificate

from .base import BackendResult

# NVIDIA NRAS GPU attestation endpoint (matches nv-attestation-sdk's NV_NRAS_GPU_URL).
DEFAULT_NRAS_URL = "https://nras.attestation.nvidia.com/v3/attest/gpu"

# Production JWKS URL where NRAS publishes its EAT (ES384) signing keys. Confirmed live and
# identical to nv-attestation-sdk's create_jwks_url(). Fetched over HTTPS — the TLS-
# authenticated endpoint is the trust anchor (see the module docstring).
DEFAULT_NRAS_JWKS_URL: Optional[str] = (
    "https://nras.attestation.nvidia.com/.well-known/jwks.json"
)

# Expected EAT issuer. NVIDIA's own SDK does NOT enforce `iss`, and the exact value is not a
# stable published contract, so we default to None (skip the check) to avoid a fail-close on
# an unverified assumption. NVIDIA's docs sample shows "https://nras.attestation.nvidia.com";
# set `expected_issuer` to that (or your tenant value) to opt into the check.
DEFAULT_EXPECTED_ISSUER: Optional[str] = None

# Optional NVIDIA attestation root CA (PEM) for defense-in-depth x5c chain pinning. NVIDIA's
# SDK pins NO root — trust is the HTTPS-fetched JWKS — so this is None by default and x5c
# chaining is skipped unless an operator supplies the genuine "NVIDIA Attestation Service CA
# 001" root (distributed by NVIDIA). When set, JWK x5c chains are verified to it.
NVIDIA_ATTESTATION_ROOT_PEM: Optional[str] = None

# Allowed clock skew (seconds) when validating exp/iat/nbf.
DEFAULT_CLOCK_SKEW_SECONDS = 120

# JWKS in-memory cache TTL (seconds).
DEFAULT_JWKS_TTL_SECONDS = 3600

# A transport is anything callable as transport(url, json_body) -> response dict. In
# production this wraps httpx; in tests it is a stub returning a canned EAT.
Transport = Callable[[str, dict], dict]

# A JWKS transport fetches the JWKS document: jwks_transport(url) -> dict. Injectable so
# tests serve a synthetic JWKS without network.
JwksTransport = Callable[[str], dict]

# Map JWS `alg` -> (kind, hash) so we know how to build the key and verify.
_EC_ALGS = {
    "ES256": (ec.SECP256R1, hashes.SHA256, 32),
    "ES384": (ec.SECP384R1, hashes.SHA384, 48),
    "ES512": (ec.SECP521R1, hashes.SHA512, 66),
}
_RS_ALGS = {
    "RS256": hashes.SHA256,
    "RS384": hashes.SHA384,
    "RS512": hashes.SHA512,
}


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _b64url_uint(value: str) -> int:
    """Decode a base64url-encoded big-endian unsigned integer (JWK n/e/x/y)."""
    return int.from_bytes(_b64url_decode(value), "big")


def decode_eat_claims(eat: str) -> dict:
    """Decode a JWS-compact EAT's payload claims WITHOUT verifying its signature.

    REAL: extracts the claim set (header.payload.signature -> payload JSON). Callers must
    not trust these claims until the JWS is verified against NRAS's JWKS (see
    ``_verify_eat_signature``).
    """
    parts = eat.split(".")
    if len(parts) != 3:
        raise ValueError("EAT is not a compact JWS (expected 3 dot-separated segments)")
    payload = _b64url_decode(parts[1])
    claims = json.loads(payload)
    if not isinstance(claims, dict):
        raise ValueError("EAT payload is not a JSON object")
    return claims


def _decode_jws_header(eat: str) -> dict:
    parts = eat.split(".")
    if len(parts) != 3:
        raise ValueError("EAT is not a compact JWS (expected 3 dot-separated segments)")
    header = json.loads(_b64url_decode(parts[0]))
    if not isinstance(header, dict):
        raise ValueError("EAT header is not a JSON object")
    return header


def _public_key_from_jwk(jwk: dict, alg: str):
    """Build a cryptography public key object from a JWK, constrained by the JWS `alg`."""
    kty = jwk.get("kty")
    if kty == "EC":
        if alg not in _EC_ALGS:
            raise ValueError(f"JWS alg {alg!r} is not an EC algorithm for an EC JWK")
        curve_cls, _hash, _coord_len = _EC_ALGS[alg]
        # Defend against a JWK whose curve disagrees with the alg.
        crv = jwk.get("crv")
        expected_crv = {"ES256": "P-256", "ES384": "P-384", "ES512": "P-521"}[alg]
        if crv is not None and crv != expected_crv:
            raise ValueError(f"JWK crv {crv!r} does not match alg {alg!r} ({expected_crv})")
        x = _b64url_uint(jwk["x"])
        y = _b64url_uint(jwk["y"])
        return ec.EllipticCurvePublicNumbers(x, y, curve_cls()).public_key()
    if kty == "RSA":
        if alg not in _RS_ALGS:
            raise ValueError(f"JWS alg {alg!r} is not an RSA algorithm for an RSA JWK")
        n = _b64url_uint(jwk["n"])
        e = _b64url_uint(jwk["e"])
        return rsa.RSAPublicNumbers(e, n).public_key()
    raise ValueError(f"unsupported JWK key type {kty!r}")


def _verify_signature(public_key, alg: str, signing_input: bytes, signature: bytes) -> None:
    """Verify a JWS signature; raises InvalidSignature / ValueError on failure."""
    if alg in _EC_ALGS:
        _curve, hash_cls, coord_len = _EC_ALGS[alg]
        if len(signature) != 2 * coord_len:
            raise ValueError(
                f"ES signature length {len(signature)} != expected {2 * coord_len} for {alg}"
            )
        r = int.from_bytes(signature[:coord_len], "big")
        s = int.from_bytes(signature[coord_len:], "big")
        der_sig = encode_dss_signature(r, s)
        public_key.verify(der_sig, signing_input, ec.ECDSA(hash_cls()))
        return
    if alg in _RS_ALGS:
        hash_cls = _RS_ALGS[alg]
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hash_cls())
        return
    raise ValueError(f"unsupported / disallowed JWS alg {alg!r}")


def _verify_x5c_chain(jwk: dict, root_pem: Optional[str]) -> tuple[bool, str]:
    """If the JWK carries an x5c, verify the leaf chains to the pinned NVIDIA root.

    REAL chain verification (each cert is signed by the next; the top chains to the pinned
    root). When ``root_pem`` is None we cannot anchor the chain, so we skip x5c chaining and
    rely on the JWKS being fetched over a TLS-pinned NRAS host (documented trust model).
    """
    x5c = jwk.get("x5c")
    if not x5c:
        return (True, "no x5c in JWK (JWKS-over-TLS is the trust anchor)")
    if root_pem is None:
        # TODO [nvidia-nras]: pin the real NVIDIA attestation root to enable x5c chaining.
        return (True, "x5c present but no pinned NVIDIA root configured; chaining skipped")

    try:
        from cryptography.x509 import load_pem_x509_certificate

        certs = [
            load_der_x509_certificate(base64.b64decode(c)) for c in x5c
        ]
        root = load_pem_x509_certificate(root_pem.encode())
    except Exception as exc:  # noqa: BLE001
        return (False, f"x5c parse failed: {exc!r}")

    # Verify each cert is signed by the next one up the chain.
    chain = certs + [root]
    for i in range(len(chain) - 1):
        child, parent = chain[i], chain[i + 1]
        try:
            _verify_cert_signed_by(child, parent)
        except Exception as exc:  # noqa: BLE001
            return (False, f"x5c chain link {i} not signed by issuer: {exc!r}")
    # And that the top of the presented chain is signed by the pinned root.
    return (True, "x5c chain verified to pinned NVIDIA root")


def _verify_cert_signed_by(child, parent) -> None:
    """Verify ``child``'s signature was produced by ``parent``'s public key."""
    pub = parent.public_key()
    if isinstance(pub, rsa.RSAPublicKey):
        pub.verify(
            child.signature,
            child.tbs_certificate_bytes,
            padding.PKCS1v15(),
            child.signature_hash_algorithm,
        )
    elif isinstance(pub, ec.EllipticCurvePublicKey):
        pub.verify(
            child.signature,
            child.tbs_certificate_bytes,
            ec.ECDSA(child.signature_hash_algorithm),
        )
    else:
        raise ValueError(f"unsupported issuer key type {type(pub).__name__}")


class _JwksCache:
    """Tiny in-memory JWKS cache with a TTL, keyed by URL."""

    def __init__(self, transport: JwksTransport, ttl: float) -> None:
        self._transport = transport
        self._ttl = ttl
        self._cache: dict[str, tuple[float, dict]] = {}

    def get(self, url: str, *, force_refresh: bool = False) -> dict:
        now = time.time()
        if not force_refresh:
            cached = self._cache.get(url)
            if cached is not None and (now - cached[0]) < self._ttl:
                return cached[1]
        jwks = self._transport(url)
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ValueError("JWKS document missing a 'keys' array")
        self._cache[url] = (now, jwks)
        return jwks

    def select_key(self, url: str, kid: Optional[str]) -> Optional[dict]:
        """Return the JWK matching ``kid`` (refreshing once on a miss for key rotation)."""
        for force in (False, True):
            jwks = self.get(url, force_refresh=force)
            keys = jwks.get("keys", [])
            for jwk in keys:
                if not isinstance(jwk, dict):
                    continue
                if kid is None:
                    # No kid in the header: only unambiguous when there's exactly one key.
                    if len(keys) == 1:
                        return jwk
                elif jwk.get("kid") == kid:
                    return jwk
            if force:
                break
        return None


def _httpx_jwks_transport(url: str) -> dict:
    """Default JWKS fetch (GET) over httpx. The JWKS host must be TLS-trusted (NRAS)."""
    import httpx  # local import: only needed for the real network path

    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _httpx_transport(url: str, json_body: dict) -> dict:
    """Default production transport (TODO: real endpoint/auth).

    TODO [nvidia-nras]: real request shape, headers/auth, timeouts, retries, and response
    envelope parsing once NRAS credentials/endpoints are available.
    """
    import httpx  # local import: only needed for the real network path

    resp = httpx.post(url, json=json_body, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def _extract_nonce_claim(claims: dict) -> Optional[str]:
    """Pull the echoed nonce from EAT claims, tolerating a few known claim names.

    NRAS EATs carry the submitted nonce echoed back; the exact claim key has varied across
    API versions, so we check the common spellings. The value is the report_data hex.
    """
    for key in ("eat_nonce", "nonce", "x-nvidia-eat-nonce"):
        val = claims.get(key)
        if isinstance(val, str):
            return val
        # Some encodings wrap the nonce in a list.
        if isinstance(val, list) and val and isinstance(val[0], str):
            return val[0]
    return None


def _extract_gpu_uuids(claims: dict) -> tuple[str, ...]:
    """Best-effort extraction of attested GPU UUIDs from EAT claims.

    NRAS overall tokens embed per-GPU sub-claims. Claim shapes vary by API version; we
    scan a few known locations. Returns () when none are found (the orchestrator still
    enforces the gpu_hash cross-check, which fails if the attested set is empty).
    """
    uuids: list[str] = []
    # Common: a submods/measurements map keyed by GPU, each with a UUID claim.
    submods = claims.get("submods") or claims.get("x-nvidia-gpu-claims") or {}
    if isinstance(submods, dict):
        for sub in submods.values():
            if isinstance(sub, dict):
                uuid = sub.get("x-nvidia-gpu-uuid") or sub.get("uuid") or sub.get("gpu_uuid")
                if isinstance(uuid, str):
                    uuids.append(uuid)
    # Or a flat list claim.
    flat = claims.get("x-nvidia-gpu-uuids") or claims.get("gpu_uuids")
    if isinstance(flat, list):
        uuids.extend(u for u in flat if isinstance(u, str))
    # Dedup, preserve order.
    seen: set[str] = set()
    out: list[str] = []
    for u in uuids:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return tuple(out)


def _validate_standard_claims(
    claims: dict,
    *,
    expected_issuer: Optional[str],
    skew: int,
    now: Optional[float] = None,
) -> tuple[bool, str]:
    """Validate iss / exp / nbf / iat with a small clock skew. Fail closed on each."""
    now = time.time() if now is None else now

    if expected_issuer is not None:
        iss = claims.get("iss")
        if iss != expected_issuer:
            return (False, f"EAT iss {iss!r} != expected issuer {expected_issuer!r}")

    exp = claims.get("exp")
    if exp is not None:
        try:
            if now > float(exp) + skew:
                return (False, f"EAT expired (exp={exp}, now={int(now)})")
        except (TypeError, ValueError):
            return (False, f"EAT exp claim is not numeric: {exp!r}")

    nbf = claims.get("nbf")
    if nbf is not None:
        try:
            if now + skew < float(nbf):
                return (False, f"EAT not yet valid (nbf={nbf}, now={int(now)})")
        except (TypeError, ValueError):
            return (False, f"EAT nbf claim is not numeric: {nbf!r}")

    iat = claims.get("iat")
    if iat is not None:
        try:
            if float(iat) > now + skew:
                return (False, f"EAT issued in the future (iat={iat}, now={int(now)})")
        except (TypeError, ValueError):
            return (False, f"EAT iat claim is not numeric: {iat!r}")

    return (True, "standard claims valid")


class NvidiaNrasBackend:
    """NVIDIA NRAS GPU-evidence backend.

    REAL: request structure, EAT JWS signature verification (against an injectable JWKS),
    standard-claim checks, and the EAT nonce-binding to the enclave report_data.

    The DEFAULT path (``trust_eat_signature=False``) performs REAL JWS verification and
    fails CLOSED if no ``jwks_url`` is configured or the signature/claims do not check out.
    ``trust_eat_signature=True`` is TEST-ONLY: it skips the cryptographic signature check so
    the nonce-binding logic can be exercised with a hand-rolled (unsigned) EAT.

    All new params are keyword-only with safe production defaults so ``NvidiaNrasBackend()``
    (used by verify.py's ``_default_gpu_backend``) keeps working — in production it fails
    closed for lack of a configured JWKS URL until NRAS onboarding fills it in.
    """

    is_secure = True

    def __init__(
        self,
        *,
        url: str = DEFAULT_NRAS_URL,
        transport: Optional[Transport] = None,
        jwks_url: Optional[str] = DEFAULT_NRAS_JWKS_URL,
        jwks_transport: Optional[JwksTransport] = None,
        jwks_ttl_seconds: float = DEFAULT_JWKS_TTL_SECONDS,
        expected_issuer: Optional[str] = DEFAULT_EXPECTED_ISSUER,
        nvidia_root_pem: Optional[str] = NVIDIA_ATTESTATION_ROOT_PEM,
        clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
        allowed_algs: tuple[str, ...] = ("ES384", "ES256", "RS256"),
        trust_eat_signature: bool = False,
    ) -> None:
        self.url = url
        self._transport = transport or _httpx_transport
        self.jwks_url = jwks_url
        self.expected_issuer = expected_issuer
        self.nvidia_root_pem = nvidia_root_pem
        self.clock_skew_seconds = clock_skew_seconds
        self.allowed_algs = tuple(allowed_algs)
        self.trust_eat_signature = trust_eat_signature
        self._jwks = _JwksCache(jwks_transport or _httpx_jwks_transport, jwks_ttl_seconds)

    # ------------------------------------------------------------------ #
    # EAT JWS signature verification (REAL)
    # ------------------------------------------------------------------ #
    def _verify_eat_signature(self, eat: str) -> tuple[bool, str]:
        """Verify the EAT JWS signature against NRAS's published JWKS.

        REAL: parses the JWS header, selects the signing key from the (cached) JWKS by
        ``kid``, rebuilds the public key from the JWK, and verifies the ES*/RS256 signature
        over ASCII ``f"{header_b64}.{payload_b64}"``. Then validates ``iss``/``exp``/``iat``/
        ``nbf`` and (when a root is pinned) the x5c chain to NVIDIA's attestation root.
        Returns (True, reason) only when signature + claims pass; else (False, reason).
        Fails CLOSED when no JWKS URL is configured.
        """
        # 1. Parse header.
        try:
            header = _decode_jws_header(eat)
        except (ValueError, json.JSONDecodeError) as exc:
            return (False, f"EAT header decode failed: {exc}")

        alg = header.get("alg")
        if not isinstance(alg, str):
            return (False, "EAT header has no 'alg'")
        # Reject 'none' and any alg we did not opt into (algorithm-confusion defense).
        if alg.lower() == "none":
            return (False, "EAT alg 'none' is rejected (unsigned token)")
        if alg not in self.allowed_algs:
            return (False, f"EAT alg {alg!r} not in allowed algs {self.allowed_algs}")
        if alg not in _EC_ALGS and alg not in _RS_ALGS:
            return (False, f"EAT alg {alg!r} unsupported")

        kid = header.get("kid")

        # 2. Fetch JWKS + select the key by kid.
        if not self.jwks_url:
            # Production with no configured JWKS: cannot establish trust -> fail CLOSED.
            return (
                False,
                "no NRAS JWKS URL configured; cannot verify EAT signature (fail-closed). "
                "Set jwks_url (TODO [nvidia-nras]: real NRAS JWKS endpoint).",
            )
        try:
            jwk = self._jwks.select_key(self.jwks_url, kid)
        except Exception as exc:  # noqa: BLE001 - surface JWKS fetch/parse failures
            return (False, f"JWKS fetch/parse failed: {exc!r}")
        if jwk is None:
            return (False, f"no JWKS key matches EAT kid {kid!r}")

        # 3. Build the public key and verify the raw signature over header.payload.
        parts = eat.split(".")
        if len(parts) != 3:
            return (False, "EAT is not a compact JWS")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        try:
            signature = _b64url_decode(parts[2])
        except Exception as exc:  # noqa: BLE001
            return (False, f"EAT signature is not valid base64url: {exc!r}")
        try:
            public_key = _public_key_from_jwk(jwk, alg)
        except (KeyError, ValueError) as exc:
            return (False, f"could not build public key from JWK: {exc}")
        try:
            _verify_signature(public_key, alg, signing_input, signature)
        except InvalidSignature:
            return (False, "EAT JWS signature is invalid (does not verify against JWKS key)")
        except ValueError as exc:
            return (False, f"EAT JWS signature verification error: {exc}")

        # 4. Optional x5c chain to the pinned NVIDIA attestation root.
        chain_ok, chain_reason = _verify_x5c_chain(jwk, self.nvidia_root_pem)
        if not chain_ok:
            return (False, f"x5c chain check failed: {chain_reason}")

        # 5. Standard claim validation (iss/exp/iat/nbf).
        try:
            claims = decode_eat_claims(eat)
        except ValueError as exc:
            return (False, f"EAT payload decode failed: {exc}")
        claims_ok, claims_reason = _validate_standard_claims(
            claims,
            expected_issuer=self.expected_issuer,
            skew=self.clock_skew_seconds,
        )
        if not claims_ok:
            return (False, claims_reason)

        return (True, f"EAT JWS signature verified ({alg}); {chain_reason}")

    # ------------------------------------------------------------------ #
    def verify(self, gpu_evidence: object, expected_nonce_hex: str) -> BackendResult:
        # 1. Submit the raw GPU evidence to NRAS. (REAL structure; mockable transport.)
        request_body = {
            # TODO [nvidia-nras]: confirm the exact request envelope NRAS expects
            # (arch, evidence list, nonce). The nonce we bind is the enclave report_data
            # hex — the same value nvattest signed over.
            "nonce": expected_nonce_hex,
            "evidence": gpu_evidence,
            "arch": "HOPPER",
        }
        try:
            response = self._transport(self.url, request_body)
        except Exception as exc:  # noqa: BLE001 - surface transport failure as a reason
            return BackendResult(
                verified=False, reason=f"NRAS request failed: {exc!r}"
            )

        # 2. Pull the EAT out of the response. NRAS returns an overall EAT token.
        eat = None
        if isinstance(response, dict):
            eat = response.get("eat") or response.get("token") or response.get("overall_token")
        if not isinstance(eat, str):
            return BackendResult(
                verified=False, reason="NRAS response did not contain an EAT token"
            )

        # 3. (REAL) verify the EAT JWS signature — fail closed unless test-trusted.
        if not self.trust_eat_signature:
            sig_ok, sig_reason = self._verify_eat_signature(eat)
            if not sig_ok:
                return BackendResult(verified=False, reason=sig_reason)
        else:
            sig_reason = "signature trust bypassed (TEST-ONLY)"

        # 4. (REAL) decode claims + enforce nonce binding.
        try:
            claims = decode_eat_claims(eat)
        except ValueError as exc:
            return BackendResult(verified=False, reason=f"EAT decode failed: {exc}")

        echoed_nonce = _extract_nonce_claim(claims)
        if echoed_nonce is None:
            return BackendResult(
                verified=False, reason="EAT has no nonce claim to bind against"
            )
        if echoed_nonce.lower() != expected_nonce_hex.lower():
            return BackendResult(
                verified=False,
                reason=(
                    "EAT nonce does not bind this request "
                    f"({echoed_nonce!r} != {expected_nonce_hex!r})"
                ),
            )

        # 5. (REAL) check NVIDIA's overall measurement result, when present.
        overall = claims.get("x-nvidia-overall-att-result")
        if overall is not None and overall not in (True, "true", "success", "OK"):
            return BackendResult(
                verified=False,
                reason=f"NRAS overall attestation result not success: {overall!r}",
            )

        measured = _extract_gpu_uuids(claims)
        return BackendResult(
            verified=True,
            measured_gpu_uuids=measured,
            measurements={"eat_claims": claims},
            reason=f"NRAS EAT nonce bound to report_data; {sig_reason}",
        )
