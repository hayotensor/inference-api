"""talaris-e2ee/v1 — end-to-end encrypted inference, the single wire-format source.

A client encrypts an OpenAI-style request body to one attested enclave's HPKE public key;
inference-api routes and bills it from plaintext METADATA only; the enclave decrypts,
runs inference, and encrypts the response back. Imported by the enclave
(``tee_wrapper``), by inference-api, and by the ``talaris-e2ee`` client. Never
re-implement any of this elsewhere. Full spec: talaris-e2ee/docs/PROTOCOL.md.

Crypto
------
Request: RFC 9180 HPKE, base mode, suite MLKEM768-X25519 / HKDF-SHA256 /
ChaCha20-Poly1305 (hybrid post-quantum). ``info`` binds every plaintext routing field
(nonce, path, model, stream, max_tokens), so a relay that edits any of them makes
decryption fail. The sealed plaintext is gzip(JSON ``{"request": <body>,
"response_key": <hex 32 bytes>}``).

Response: the client's random 32-byte ``response_key`` travels inside the request, so
only the client and the enclave hold it. Per-purpose keys are HKDF-SHA256 from it, with
the request nonce in ``info`` (a response cannot be replayed onto another request).
ChaCha20-Poly1305 throughout.

  * non-streaming: ``nonce(12) || ct||tag``, AAD = label || HTTP status (3 ASCII digits).
  * streaming: one SSE event per engine event, AEAD nonce = 96-bit big-endian sequence
    number, AAD = label || seq(8) || final(1). Ordering, drops and truncation are all
    detectable: seqs must be contiguous from 0, and the stream is complete only after
    the ``final`` event.

Billing metadata (token usage) is deliberately PLAINTEXT, outside the ciphertext: the
``X-Talaris-Usage`` header (non-stream) or a ``{"usage": ...}`` SSE event (stream).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, hpke
from cryptography.hazmat.primitives.asymmetric import mlkem, x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

E2EE_VERSION = "v1"

SUITE = hpke.Suite(hpke.KEM.MLKEM768_X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.CHACHA20_POLY1305)
ENC_LEN = hpke.KEM.MLKEM768_X25519.enc_length()  # 1120
MLKEM_PUBLIC_KEY_LEN = 1184
X25519_PUBLIC_KEY_LEN = 32
PUBLIC_KEY_LEN = MLKEM_PUBLIC_KEY_LEN + X25519_PUBLIC_KEY_LEN  # 1216

NONCE_LEN = 32  # enclave-issued, single-use
RESPONSE_KEY_LEN = 32
TAG_LEN = 16
MAX_PLAINTEXT_BYTES = 8 * 1024 * 1024  # decompressed request JSON
MAX_MAX_TOKENS = 1_000_000

ALLOWED_PATHS = ("/v1/chat/completions", "/v1/completions")

# Domain-separation labels. Never reuse one for another purpose.
_REQUEST_INFO = b"talaris-e2ee/v1/request"
_RESPONSE_KEY_INFO = b"talaris-e2ee/v1/response-key"
_STREAM_KEY_INFO = b"talaris-e2ee/v1/stream-key"
_RESPONSE_AAD = b"talaris-e2ee/v1/response"
_STREAM_AAD = b"talaris-e2ee/v1/stream"

# Plaintext metadata headers. Everything but INSTANCE is bound into the HPKE info.
H_VERSION = "X-E2EE-Version"
H_NONCE = "X-E2EE-Nonce"
H_PATH = "X-E2EE-Path"
H_MODEL = "X-E2EE-Model"
H_STREAM = "X-E2EE-Stream"
H_MAX_TOKENS = "X-E2EE-Max-Tokens"
H_INSTANCE = "X-E2EE-Instance"  # API routing only; the enclave key already binds it
# Response headers.
H_ENCRYPTED = "X-E2EE-Encrypted"  # "1" when the body is a sealed response
H_ERROR = "X-E2EE-Error"  # protocol error code on a plaintext error
H_RETRY = "X-E2EE-Retry"  # "1" when re-encrypting to another instance may succeed
H_USAGE = "X-Talaris-Usage"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

# code -> (HTTP status, retryable). Retryable means: fetch a fresh nonce (or another
# instance), re-encrypt, and try again. Protocol errors never carry request content.
ERROR_CODES: dict[str, tuple[int, bool]] = {
    "unsupported_version": (400, False),
    "bad_metadata": (400, False),
    "path_not_allowed": (400, False),
    "metadata_mismatch": (400, False),
    "decrypt_failed": (400, False),
    "payload_too_large": (413, False),
    "nonce_unknown": (409, True),
    "e2ee_unavailable": (503, True),
    "instance_unavailable": (503, True),
}


class E2EEError(Exception):
    """A talaris-e2ee protocol failure with a stable ``code``."""

    def __init__(self, code: str, message: str = "") -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown E2EE error code {code!r}")
        super().__init__(message or code)
        self.code = code
        self.status, self.retryable = ERROR_CODES[code]

    def to_body(self) -> dict:
        return {"error": {"code": self.code, "message": str(self), "type": "e2ee_error"}}

    def to_headers(self) -> dict[str, str]:
        return {H_ERROR: self.code, H_RETRY: "1" if self.retryable else "0"}


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


@dataclass
class E2EEKeyPair:
    """The enclave's HPKE keypair. Generated at boot; the private half never leaves memory."""

    _mlkem: mlkem.MLKEM768PrivateKey
    _x25519: x25519.X25519PrivateKey
    _private: hpke.MLKEM768X25519PrivateKey = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._private = hpke.MLKEM768X25519PrivateKey(self._mlkem, self._x25519)

    @classmethod
    def generate(cls) -> "E2EEKeyPair":
        return cls(mlkem.MLKEM768PrivateKey.generate(), x25519.X25519PrivateKey.generate())

    @property
    def public_bytes(self) -> bytes:
        """ML-KEM-768 public key (1184) || X25519 public key (32)."""
        return (
            self._mlkem.public_key().public_bytes_raw()
            + self._x25519.public_key().public_bytes_raw()
        )

    def decrypt(self, ciphertext: bytes, info: bytes) -> bytes:
        return SUITE.decrypt(ciphertext, self._private, info)


def public_key_from_bytes(raw: bytes) -> hpke.MLKEM768X25519PublicKey:
    if len(raw) != PUBLIC_KEY_LEN:
        raise ValueError(f"E2EE public key must be {PUBLIC_KEY_LEN} bytes, got {len(raw)}")
    return hpke.MLKEM768X25519PublicKey(
        mlkem.MLKEM768PublicKey.from_public_bytes(raw[:MLKEM_PUBLIC_KEY_LEN]),
        x25519.X25519PublicKey.from_public_bytes(raw[MLKEM_PUBLIC_KEY_LEN:]),
    )


def key_id(public_key: bytes) -> str:
    """Short stable identifier for an E2EE public key (sha256 hex)."""
    return hashlib.sha256(public_key).hexdigest()


# --------------------------------------------------------------------------- #
# Request metadata
# --------------------------------------------------------------------------- #


def _lp(*fields: bytes) -> bytes:
    return b"".join(len(f).to_bytes(4, "big") + f for f in fields)


@dataclass(frozen=True)
class RequestMeta:
    """The plaintext routing/billing fields of one E2EE request.

    The relay (inference-api) reads these to route, enforce the key's model allowlist,
    and reserve credit; the enclave refuses a payload that disagrees with them.
    """

    nonce: str  # hex, enclave-issued
    path: str
    model: str
    stream: bool
    max_tokens: int

    def __post_init__(self) -> None:
        try:
            raw = bytes.fromhex(self.nonce)
        except ValueError as exc:
            raise E2EEError("bad_metadata", "nonce must be hex") from exc
        if len(raw) != NONCE_LEN:
            raise E2EEError("bad_metadata", f"nonce must be {NONCE_LEN} bytes")
        if self.nonce != self.nonce.lower():
            raise E2EEError("bad_metadata", "nonce must be lowercase hex")
        if self.path not in ALLOWED_PATHS:
            raise E2EEError("path_not_allowed", f"path {self.path!r} is not E2EE-capable")
        if not self.model or len(self.model) > 256:
            raise E2EEError("bad_metadata", "model must be 1-256 characters")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise E2EEError("bad_metadata", "max_tokens must be an integer")
        if not 1 <= self.max_tokens <= MAX_MAX_TOKENS:
            raise E2EEError("bad_metadata", f"max_tokens must be 1..{MAX_MAX_TOKENS}")

    def info(self) -> bytes:
        return _lp(
            _REQUEST_INFO,
            bytes.fromhex(self.nonce),
            self.path.encode(),
            self.model.encode(),
            b"1" if self.stream else b"0",
            str(self.max_tokens).encode(),
        )

    def to_headers(self) -> dict[str, str]:
        return {
            H_VERSION: E2EE_VERSION,
            H_NONCE: self.nonce,
            H_PATH: self.path,
            H_MODEL: self.model,
            H_STREAM: "true" if self.stream else "false",
            H_MAX_TOKENS: str(self.max_tokens),
        }

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "RequestMeta":
        """Parse from (case-insensitive) HTTP headers, e.g. starlette/httpx Headers."""
        version = headers.get(H_VERSION)
        if version != E2EE_VERSION:
            raise E2EEError("unsupported_version", f"{H_VERSION} must be {E2EE_VERSION!r}")
        missing = [h for h in (H_NONCE, H_PATH, H_MODEL, H_STREAM, H_MAX_TOKENS) if not headers.get(h)]
        if missing:
            raise E2EEError("bad_metadata", f"missing headers: {', '.join(missing)}")
        stream = headers[H_STREAM]
        if stream not in ("true", "false"):
            raise E2EEError("bad_metadata", f"{H_STREAM} must be 'true' or 'false'")
        raw_max = headers[H_MAX_TOKENS]
        if not raw_max.isascii() or not raw_max.isdigit():
            raise E2EEError("bad_metadata", f"{H_MAX_TOKENS} must be a decimal integer")
        return cls(
            nonce=headers[H_NONCE],
            path=headers[H_PATH],
            model=headers[H_MODEL],
            stream=stream == "true",
            max_tokens=int(raw_max),
        )


# --------------------------------------------------------------------------- #
# Request sealing
# --------------------------------------------------------------------------- #


def seal_request(public_key: bytes, meta: RequestMeta, body: Mapping[str, Any]) -> tuple[bytes, bytes]:
    """Client side: encrypt ``body`` to the enclave. Returns (blob, response_key)."""
    response_key = os.urandom(RESPONSE_KEY_LEN)
    plaintext = gzip.compress(
        json.dumps({"request": dict(body), "response_key": response_key.hex()},
                   separators=(",", ":")).encode()
    )
    blob = SUITE.encrypt(plaintext, public_key_from_bytes(public_key), meta.info())
    return blob, response_key


def _bounded_gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(data, MAX_PLAINTEXT_BYTES + 1)
    except zlib.error as exc:
        raise E2EEError("decrypt_failed", "payload is not valid gzip") from exc
    if len(out) > MAX_PLAINTEXT_BYTES or d.unconsumed_tail:
        raise E2EEError("payload_too_large", f"decompressed payload exceeds {MAX_PLAINTEXT_BYTES} bytes")
    if not d.eof:
        raise E2EEError("decrypt_failed", "truncated gzip payload")
    return out


def _token_limit(body: dict, path: str) -> tuple[str, Optional[int]]:
    """The body's own completion-token limit and which field holds it."""
    fields = ("max_completion_tokens", "max_tokens") if path == "/v1/chat/completions" else ("max_tokens",)
    for name in fields:
        if body.get(name) is not None:
            value = body[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise E2EEError("metadata_mismatch", f"{name} must be an integer")
            return name, value
    return "max_tokens", None


def open_request(keypair: E2EEKeyPair, meta: RequestMeta, blob: bytes) -> tuple[dict, bytes]:
    """Enclave side: decrypt and validate. Returns (request body, response_key).

    The body's ``model`` and ``stream`` must equal the plaintext metadata, and its
    completion-token limit may not exceed ``meta.max_tokens`` (it is set to that value
    when absent) — so the metadata the relay billed on is what actually runs.
    """
    if len(blob) <= ENC_LEN + TAG_LEN:
        raise E2EEError("decrypt_failed", "ciphertext too short")
    try:
        compressed = keypair.decrypt(blob, meta.info())
    except (InvalidTag, ValueError) as exc:
        raise E2EEError("decrypt_failed", "could not decrypt request (wrong key or altered metadata)") from exc
    try:
        envelope = json.loads(_bounded_gunzip(compressed))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise E2EEError("decrypt_failed", "decrypted payload is not JSON") from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("request"), dict):
        raise E2EEError("decrypt_failed", "payload must be {request, response_key}")
    try:
        response_key = bytes.fromhex(envelope.get("response_key") or "")
    except (TypeError, ValueError) as exc:
        raise E2EEError("decrypt_failed", "response_key must be hex") from exc
    if len(response_key) != RESPONSE_KEY_LEN:
        raise E2EEError("decrypt_failed", f"response_key must be {RESPONSE_KEY_LEN} bytes")

    body = envelope["request"]
    if body.get("model") != meta.model:
        raise E2EEError("metadata_mismatch", "body model differs from the routed model")
    if bool(body.get("stream", False)) != meta.stream:
        raise E2EEError("metadata_mismatch", "body stream flag differs from the routed one")
    name, limit = _token_limit(body, meta.path)
    if limit is None:
        body[name] = meta.max_tokens
    elif not 1 <= limit <= meta.max_tokens:
        raise E2EEError("metadata_mismatch", f"{name} exceeds the routed max_tokens")
    return body, response_key


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


def _derive(response_key: bytes, label: bytes, nonce_hex: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=_lp(label, bytes.fromhex(nonce_hex))).derive(response_key)


def _status_aad(status: int) -> bytes:
    if not 100 <= status <= 599:
        raise ValueError("HTTP status out of range")
    return _RESPONSE_AAD + str(status).encode()


def seal_response(response_key: bytes, meta: RequestMeta, status: int, body: bytes) -> bytes:
    """Enclave side: seal a complete (non-streaming or error) response body."""
    key = _derive(response_key, _RESPONSE_KEY_INFO, meta.nonce)
    nonce = os.urandom(12)
    return nonce + ChaCha20Poly1305(key).encrypt(nonce, body, _status_aad(status))


def open_response(response_key: bytes, meta: RequestMeta, status: int, blob: bytes) -> bytes:
    """Client side: open a sealed response. The status is authenticated too."""
    if len(blob) < 12 + TAG_LEN:
        raise E2EEError("decrypt_failed", "sealed response too short")
    key = _derive(response_key, _RESPONSE_KEY_INFO, meta.nonce)
    try:
        return ChaCha20Poly1305(key).decrypt(blob[:12], blob[12:], _status_aad(status))
    except InvalidTag as exc:
        raise E2EEError("decrypt_failed", "response failed authentication") from exc


def _stream_aad(seq: int, final: bool) -> bytes:
    return _STREAM_AAD + seq.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


class StreamSealer:
    """Enclave side: seals one SSE stream. One instance per request."""

    def __init__(self, response_key: bytes, meta: RequestMeta) -> None:
        self._aead = ChaCha20Poly1305(_derive(response_key, _STREAM_KEY_INFO, meta.nonce))
        self._seq = 0
        self._finished = False

    def _seal(self, data: bytes, final: bool) -> tuple[int, str]:
        if self._finished:
            raise RuntimeError("stream already finalized")
        seq = self._seq
        self._seq += 1
        self._finished = final
        ct = self._aead.encrypt(seq.to_bytes(12, "big"), data, _stream_aad(seq, final))
        return seq, base64.b64encode(ct).decode()

    def chunk(self, data: str) -> dict:
        """Seal one engine SSE ``data:`` payload (the text after ``data: ``)."""
        seq, ct = self._seal(data.encode(), final=False)
        return {"e2ee": ct, "seq": seq}

    def final(self, trailer: Mapping[str, Any]) -> dict:
        """Seal the closing event. ``trailer`` is e.g. {"usage": ...} or {"error": ...}."""
        seq, ct = self._seal(json.dumps(dict(trailer), separators=(",", ":")).encode(), final=True)
        return {"e2ee_final": ct, "seq": seq}


class StreamOpener:
    """Client side: opens one sealed SSE stream, enforcing order and completeness."""

    def __init__(self, response_key: bytes, meta: RequestMeta) -> None:
        self._aead = ChaCha20Poly1305(_derive(response_key, _STREAM_KEY_INFO, meta.nonce))
        self._next = 0
        self.trailer: Optional[dict] = None

    @property
    def finished(self) -> bool:
        return self.trailer is not None

    def _open(self, event: Mapping[str, Any], key: str, final: bool) -> bytes:
        if self.finished:
            raise E2EEError("decrypt_failed", "event after the final stream event")
        seq = event.get("seq")
        if seq != self._next:
            raise E2EEError("decrypt_failed", f"stream event out of order (got {seq!r}, want {self._next})")
        try:
            ct = base64.b64decode(event[key], validate=True)
            data = self._aead.decrypt(seq.to_bytes(12, "big"), ct, _stream_aad(seq, final))
        except (InvalidTag, ValueError, TypeError) as exc:
            raise E2EEError("decrypt_failed", "stream event failed authentication") from exc
        self._next += 1
        return data

    def open(self, event: Mapping[str, Any]) -> Optional[str]:
        """Open one event. Returns the decrypted engine payload, or None for the final
        event (whose trailer is then in ``self.trailer``) and for plaintext usage events.
        """
        if "e2ee" in event:
            return self._open(event, "e2ee", final=False).decode()
        if "e2ee_final" in event:
            trailer = json.loads(self._open(event, "e2ee_final", final=True))
            self.trailer = trailer if isinstance(trailer, dict) else {}
            return None
        if "usage" in event and len(event) == 1:
            return None  # plaintext billing copy; the authenticated one is in the trailer
        raise E2EEError("decrypt_failed", "unexpected plaintext event in an E2EE stream")


def sse_event(payload: Mapping[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(dict(payload), separators=(",", ":"))
    return f"data: {data}\n\n".encode()


# --------------------------------------------------------------------------- #
# Usage (plaintext billing metadata)
# --------------------------------------------------------------------------- #


def format_usage(prompt_tokens: Optional[int], completion_tokens: Optional[int],
                 total_tokens: Optional[int]) -> str:
    return json.dumps({"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                       "total_tokens": total_tokens}, separators=(",", ":"))


def parse_usage(value: Optional[str]) -> Optional[dict]:
    if not value:
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    out = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = data.get(k)
        out[k] = v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    return out


__all__ = [
    "E2EE_VERSION", "SUITE", "ENC_LEN", "PUBLIC_KEY_LEN", "NONCE_LEN", "RESPONSE_KEY_LEN",
    "MAX_PLAINTEXT_BYTES", "ALLOWED_PATHS", "ERROR_CODES",
    "H_VERSION", "H_NONCE", "H_PATH", "H_MODEL", "H_STREAM", "H_MAX_TOKENS", "H_INSTANCE",
    "H_ENCRYPTED", "H_ERROR", "H_RETRY", "H_USAGE",
    "E2EEError", "E2EEKeyPair", "public_key_from_bytes", "key_id",
    "RequestMeta", "seal_request", "open_request",
    "seal_response", "open_response", "StreamSealer", "StreamOpener", "sse_event",
    "format_usage", "parse_usage",
]
