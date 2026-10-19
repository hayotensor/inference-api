"""TEEForwarder: forward an inference request to a selected miner's enclave.

Decrypts the miner's active provisioned token, calls the enclave's
``POST /v1/chat/completions`` over a TLS-leaf-pinned client, and returns the
completion plus the REAL token usage reported by the enclave (never an estimate
from ``len(text.split())``).

Streaming is passed through unchanged while the SSE ``usage`` chunk is
accumulated so the caller can settle from real counts even if the client
disconnects mid-stream. On a ``401`` the token is re-read once and retried; on a
pin mismatch the miner is marked ``stale``.

Every attempt — successful or not — is TIMED and given a stable ``outcome`` code.
This is the platform's only first-hand measurement of how a miner actually
performed, and it is what the per-epoch miner statistics (and therefore on-chain
scoring) are built from, so a failure must carry the same detail as a success
rather than being collapsed into a bare exception.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_attest import CertFingerprintMismatch
from talaris_contracts.e2ee import parse_usage

from inference_api.config import settings
from inference_api.crypto import decrypt_token
from inference_api.logging import request_id_ctx
from inference_api.models import Miner, ProvisionedToken
from inference_api.routing.pinning import PinError, build_enclave_client
from inference_api.routing.selector import MinerCandidate
from inference_api.security import utcnow

logger = logging.getLogger(__name__)

# Stable per-attempt outcome codes. These are persisted on every
# ``miner_request_stats`` row and aggregated into the signed epoch snapshot, so
# treat them as a wire format: add new codes, never repurpose an existing one.
OUTCOME_SUCCESS = "success"
OUTCOME_FORWARD_ERROR = "forward_error"
OUTCOME_HTTP_ERROR = "http_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_PIN_MISMATCH = "pin_mismatch"
OUTCOME_NO_TOKEN = "no_token"
# A pinned client could not even be built (pinning enforced, no attested
# fingerprint on record). Distinct from pin_mismatch: nothing was dialed.
OUTCOME_NO_PIN = "no_pin"


def _elapsed_ms(started: float) -> int:
    """Whole milliseconds since a ``time.perf_counter()`` mark (never negative)."""
    return max(0, int((time.perf_counter() - started) * 1000))


def _transport_outcome(exc: httpx.HTTPError) -> str:
    """Distinguish a timeout from any other transport failure.

    A timeout means the miner accepted the connection and then failed to answer in
    time, which is a different quality signal from a refused/broken connection.
    """
    return OUTCOME_TIMEOUT if isinstance(exc, httpx.TimeoutException) else OUTCOME_FORWARD_ERROR


class ForwardError(RuntimeError):
    """A forward attempt failed (transport, auth, pin, or upstream error).

    Carries the measurement the caller needs to record the failed attempt:
    ``outcome`` (one of the ``OUTCOME_*`` codes), how long it took, and the
    upstream status if there was one. Subclasses override the class-level
    ``outcome`` default.
    """

    outcome = OUTCOME_FORWARD_ERROR

    def __init__(
        self,
        message: str,
        *,
        outcome: str | None = None,
        latency_ms: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        if outcome is not None:
            self.outcome = outcome
        self.latency_ms = latency_ms
        self.http_status = http_status


class NoActiveTokenError(ForwardError):
    """The selected miner has no active provisioned token to forward with."""

    outcome = OUTCOME_NO_TOKEN


class NoPinError(ForwardError):
    """A pinned enclave client could not be built (``pinning.PinError``).

    Surfaced as a ``ForwardError`` so every caller's ``except ForwardError``
    (failover, reservation release) handles it. Before this, a raw ``PinError``
    escaped the streaming path after its reservation was committed, leaking the
    reservation until its TTL.
    """

    outcome = OUTCOME_NO_PIN


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict | None) -> "Usage":
        if not isinstance(data, dict):
            return cls()
        prompt = int(data.get("prompt_tokens", 0) or 0)
        completion = int(data.get("completion_tokens", 0) or 0)
        total = int(data.get("total_tokens", 0) or (prompt + completion))
        return cls(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


@dataclass
class ForwardResult:
    candidate: MinerCandidate
    status_code: int
    body: dict | None
    usage: Usage
    streamed: bool = False
    # Wall time the platform waited on this miner, including a 401 token re-read.
    latency_ms: int = 0


@dataclass
class StreamForward:
    """A live streaming forward; iterate ``aiter()`` then read ``usage``.

    ``latency_ms`` starts as the time to obtain response headers and is extended to
    the full stream duration once the stream closes; ``ttft_ms`` is the time to the
    first non-empty SSE payload — the number that actually reflects perceived
    responsiveness, and the reason it is measured here rather than inferred later.
    """

    candidate: MinerCandidate
    response: httpx.Response
    client: httpx.AsyncClient
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    ttft_ms: int | None = None
    # SSE chunks that carried generated text, as relayed to the client. Engines
    # emit roughly one token per content delta, so this is the billing fallback
    # when a client hangs up after receiving the text but before the trailing
    # ``usage`` chunk (HYP-56) -- see product_routes._routed_stream_response.
    content_chunks: int = 0
    _started: float = field(default_factory=time.perf_counter)
    _settled: bool = False

    async def aiter(self) -> AsyncIterator[bytes]:
        try:
            async for raw in self.response.aiter_bytes():
                self._accumulate(raw)
                yield raw
        finally:
            await self.aclose()

    def _accumulate(self, raw: bytes) -> None:
        # Parse SSE ``data:`` lines, capturing any ``usage`` object emitted.
        for line in raw.split(b"\n"):
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[len(b"data:"):].strip()
            if not payload or payload == b"[DONE]":
                continue
            if self.ttft_ms is None:
                self.ttft_ms = _elapsed_ms(self._started)
            try:
                obj = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                continue
            usage = obj.get("usage") if isinstance(obj, dict) else None
            if isinstance(usage, dict):
                self.usage = Usage.from_dict(usage)
            choices = obj.get("choices") if isinstance(obj, dict) else None
            if isinstance(choices, list):
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    text = (
                        (delta.get("content") or delta.get("reasoning_content"))
                        if isinstance(delta, dict)
                        else None
                    ) or choice.get("text")
                    if text:
                        self.content_chunks += 1

    async def aclose(self) -> None:
        if self._settled:
            return
        self._settled = True
        # Extend the header-only latency to the full stream duration. Done before
        # closing so it is populated even if teardown raises.
        self.latency_ms = _elapsed_ms(self._started)
        try:
            await self.response.aclose()
        finally:
            await self.client.aclose()


class TEEForwarder:
    def __init__(self, session: AsyncSession, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.session = session
        # Injected transport (tests) -> all enclave clients use it; pinning bypassed.
        self._transport = transport

    async def _active_token(self, miner_id, model_id) -> tuple[ProvisionedToken, str]:
        result = await self.session.execute(
            select(ProvisionedToken).where(
                ProvisionedToken.miner_id == miner_id,
                ProvisionedToken.status == "active",
                ProvisionedToken.model_id == model_id,
            )
        )
        token = result.scalars().first()
        if token is None:
            raise NoActiveTokenError(f"miner {miner_id} has no active token")
        return token, decrypt_token(token.encrypted_token)

    async def _mark_stale(self, miner_id) -> None:
        miner = await self.session.get(Miner, miner_id)
        if miner is not None:
            miner.attestation_status = "stale"
            self.session.add(miner)
            await self.session.flush()

    def _client(
        self,
        candidate: MinerCandidate,
        bearer: str | None,
        *,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.AsyncClient:
        try:
            return build_enclave_client(
                base_url=candidate.tee_endpoint,
                tls_cert_fingerprint=candidate.tls_cert_fingerprint,
                bearer=bearer,
                transport=self._transport,
                timeout=timeout,
            )
        except PinError as exc:
            raise NoPinError(str(exc), latency_ms=0) from exc

    def _headers(self, bearer: str, request_id: str | None) -> dict[str, str]:
        """Auth + request-id headers for an enclave call.

        The enclave stamps ``X-Request-Id`` onto the usage row it records, so this
        header is what later lets the platform match its own attempt record against
        the enclave's signed receipt. Callers pass the id they persisted rather than
        relying on the ambient context, so the two sides agree by construction.
        """
        rid = request_id or request_id_ctx.get()
        return {
            "Authorization": f"Bearer {bearer}",
            **({"X-Request-Id": rid} if rid else {}),
        }

    async def forward(
        self, candidate: MinerCandidate, payload: dict, *, request_id: str | None = None
    ) -> ForwardResult:
        """Non-streaming forward. Retries once on 401 after re-reading the token.

        The timer spans the whole call (including a token re-read) because that is
        what the caller actually waited on. Every exit path — success or any
        failure — reports a measured ``latency_ms``.
        """
        started = time.perf_counter()
        for attempt in (1, 2):
            token_row, bearer = await self._active_token(candidate.miner_id, candidate.model_id)
            client = self._client(candidate, bearer)
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={**payload, "stream": False},
                    headers=self._headers(bearer, request_id),
                )
            except CertFingerprintMismatch as exc:
                await self._mark_stale(candidate.miner_id)
                raise ForwardError(
                    f"TLS pin mismatch: {exc}",
                    outcome=OUTCOME_PIN_MISMATCH,
                    latency_ms=_elapsed_ms(started),
                ) from exc
            except httpx.HTTPError as exc:
                raise ForwardError(
                    f"forward transport error: {exc}",
                    outcome=_transport_outcome(exc),
                    latency_ms=_elapsed_ms(started),
                ) from exc
            finally:
                await client.aclose()

            if resp.status_code == 401 and attempt == 1:
                # Token may have rotated; re-read and retry once.
                continue
            if resp.status_code >= 400:
                raise ForwardError(
                    f"enclave returned {resp.status_code} for {candidate.hotkey[:12]}...",
                    outcome=OUTCOME_HTTP_ERROR,
                    latency_ms=_elapsed_ms(started),
                    http_status=resp.status_code,
                )
            body = resp.json()
            usage = Usage.from_dict(body.get("usage") if isinstance(body, dict) else None)
            return ForwardResult(
                candidate=candidate,
                status_code=resp.status_code,
                body=body,
                usage=usage,
                latency_ms=_elapsed_ms(started),
            )
        raise ForwardError(
            "forward failed after token re-read",
            outcome=OUTCOME_HTTP_ERROR,
            latency_ms=_elapsed_ms(started),
            http_status=401,
        )

    async def forward_stream(
        self, candidate: MinerCandidate, payload: dict, *, request_id: str | None = None
    ) -> StreamForward:
        """Open a streaming forward. Caller iterates and then reads ``.usage``."""
        started = time.perf_counter()
        token_row, bearer = await self._active_token(candidate.miner_id, candidate.model_id)
        client = self._client(candidate, bearer)
        stream_payload = {
            **payload,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        request = client.build_request(
            "POST",
            "/v1/chat/completions",
            json=stream_payload,
            headers=self._headers(bearer, request_id),
        )
        try:
            response = await client.send(request, stream=True)
        except CertFingerprintMismatch as exc:
            await client.aclose()
            await self._mark_stale(candidate.miner_id)
            raise ForwardError(
                f"TLS pin mismatch: {exc}",
                outcome=OUTCOME_PIN_MISMATCH,
                latency_ms=_elapsed_ms(started),
            ) from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            raise ForwardError(
                f"forward transport error: {exc}",
                outcome=_transport_outcome(exc),
                latency_ms=_elapsed_ms(started),
            ) from exc
        if response.status_code >= 400:
            await response.aclose()
            await client.aclose()
            raise ForwardError(
                f"enclave returned {response.status_code}",
                outcome=OUTCOME_HTTP_ERROR,
                latency_ms=_elapsed_ms(started),
                http_status=response.status_code,
            )
        return StreamForward(
            candidate=candidate,
            response=response,
            client=client,
            latency_ms=_elapsed_ms(started),
            _started=started,
        )

    # ------------------------------------------------------------------ #
    # talaris-e2ee/v1 relay. The platform only ever moves ciphertext here; the
    # same token lookup, pinned client and pin-mismatch classification as the
    # plaintext path are reused so the E2EE path cannot weaken either.
    #
    # These helpers never touch ``self.session`` after ``active_bearer`` so the
    # instance listing can dial several enclaves concurrently; callers apply
    # ``mark_stale`` for an ``OUTCOME_PIN_MISMATCH`` error afterwards.
    # ------------------------------------------------------------------ #

    async def active_bearer(self, candidate: MinerCandidate) -> str:
        """The decrypted inference token for ``candidate`` (``NoActiveTokenError``)."""
        _row, bearer = await self._active_token(candidate.miner_id, candidate.model_id)
        return bearer

    async def mark_stale(self, miner_id) -> None:
        """Mark a miner ``stale`` exactly as a TLS pin mismatch does."""
        await self._mark_stale(miner_id)

    async def _open(
        self,
        candidate: MinerCandidate,
        method: str,
        path: str,
        *,
        bearer: str | None,
        request_id: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        **kwargs,
    ) -> "EnclaveExchange":
        """Send one request over the (pinned) enclave client, response NOT read.

        Transport/pin failures raise ``ForwardError`` (pin mismatch carries
        ``OUTCOME_PIN_MISMATCH``; the caller marks the miner stale). Any HTTP
        status is returned to the caller, which owns closing the exchange.
        """
        started = time.perf_counter()
        client = self._client(candidate, bearer, timeout=timeout)
        headers = {**kwargs.pop("headers", {})}
        if bearer:
            headers.update(self._headers(bearer, request_id))
        request = client.build_request(method, path, headers=headers, **kwargs)
        try:
            response = await client.send(request, stream=True)
        except CertFingerprintMismatch as exc:
            await client.aclose()
            raise ForwardError(
                f"TLS pin mismatch: {exc}",
                outcome=OUTCOME_PIN_MISMATCH,
                latency_ms=_elapsed_ms(started),
            ) from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            raise ForwardError(
                f"forward transport error: {exc}",
                outcome=_transport_outcome(exc),
                latency_ms=_elapsed_ms(started),
            ) from exc
        return EnclaveExchange(
            candidate=candidate,
            response=response,
            client=client,
            latency_ms=_elapsed_ms(started),
            _started=started,
        )

    async def _read_json(self, exchange: "EnclaveExchange") -> dict:
        try:
            if exchange.response.status_code != 200:
                raise ForwardError(
                    f"enclave returned {exchange.response.status_code}",
                    outcome=OUTCOME_HTTP_ERROR,
                    latency_ms=_elapsed_ms(exchange._started),
                    http_status=exchange.response.status_code,
                )
            try:
                await exchange.response.aread()
                body = exchange.response.json()
            except httpx.HTTPError as exc:
                raise ForwardError(
                    f"forward transport error: {exc}",
                    outcome=_transport_outcome(exc),
                    latency_ms=_elapsed_ms(exchange._started),
                ) from exc
            except ValueError as exc:
                raise ForwardError(
                    "enclave returned a non-JSON body",
                    outcome=OUTCOME_HTTP_ERROR,
                    latency_ms=_elapsed_ms(exchange._started),
                    http_status=exchange.response.status_code,
                ) from exc
            if not isinstance(body, dict):
                raise ForwardError(
                    "enclave returned a non-object JSON body",
                    outcome=OUTCOME_HTTP_ERROR,
                    latency_ms=_elapsed_ms(exchange._started),
                    http_status=exchange.response.status_code,
                )
            return body
        finally:
            await exchange.aclose()

    async def e2ee_nonces(
        self, candidate: MinerCandidate, bearer: str, count: int, *, timeout: float | None = None
    ) -> dict:
        """``POST /e2e/nonces`` -> the enclave's JSON (nonces, expires_in, e2ee_key, key_id)."""
        exchange = await self._open(
            candidate, "POST", "/e2e/nonces", bearer=bearer, json={"count": count}, timeout=timeout
        )
        return await self._read_json(exchange)

    async def e2ee_attestation(self, candidate: MinerCandidate, nonce_hex: str) -> bytes:
        """``GET /attestation?nonce=`` -> the raw JSON bytes, verbatim (the client verifies)."""
        exchange = await self._open(
            candidate,
            "GET",
            "/attestation",
            bearer=None,
            params={"nonce": nonce_hex},
            timeout=httpx.Timeout(
                settings.tee_attestation_timeout_seconds,
                connect=settings.tee_connect_timeout_seconds,
            ),
        )
        try:
            if exchange.response.status_code != 200:
                raise ForwardError(
                    f"enclave returned {exchange.response.status_code}",
                    outcome=OUTCOME_HTTP_ERROR,
                    latency_ms=_elapsed_ms(exchange._started),
                    http_status=exchange.response.status_code,
                )
            try:
                return await exchange.response.aread()
            except httpx.HTTPError as exc:
                raise ForwardError(
                    f"forward transport error: {exc}",
                    outcome=_transport_outcome(exc),
                    latency_ms=_elapsed_ms(exchange._started),
                ) from exc
        finally:
            await exchange.aclose()

    async def e2ee_invoke(
        self,
        candidate: MinerCandidate,
        bearer: str,
        body: bytes,
        headers: dict[str, str],
        *,
        request_id: str | None = None,
    ) -> "EnclaveExchange":
        """``POST /e2e/invoke`` with the ciphertext; the response is NOT read.

        No failover and no 401 retry: the ciphertext opens only on this enclave,
        and re-sending it after the enclave consumed the nonce could only fail.
        """
        return await self._open(
            candidate,
            "POST",
            "/e2e/invoke",
            bearer=bearer,
            request_id=request_id,
            content=body,
            headers={**headers, "Content-Type": "application/octet-stream"},
        )


@dataclass
class EnclaveExchange(StreamForward):
    """One open E2EE enclave response (status/headers known, body not yet read).

    Subclasses ``StreamForward`` so ``stats_recorder.record_stream_success`` and the
    timing fields apply unchanged; ``_accumulate`` is replaced with an SSE parser
    for the talaris-e2ee/v1 stream shape:

      * ``{"usage": {...}}``   the enclave's PLAINTEXT billing copy -> ``usage``;
      * ``{"e2ee": ..}``       one sealed engine event -> ``content_chunks``, the
                               HYP-56 disconnect-billing fallback.

    Lines are buffered across network reads, so an event split between two TCP
    segments is still parsed (the bytes relayed to the client are untouched).
    """

    usage_seen: bool = False
    _buffer: bytes = b""

    # An event line longer than this is not a usage event worth parsing; drop it
    # rather than buffering an unbounded line from a misbehaving enclave.
    _MAX_LINE = 1024 * 1024

    def _accumulate(self, raw: bytes) -> None:
        data = self._buffer + raw
        lines = data.split(b"\n")
        self._buffer = lines.pop()
        if len(self._buffer) > self._MAX_LINE:
            self._buffer = b""
        for line in lines:
            self._parse_line(line.strip())

    def _parse_line(self, line: bytes) -> None:
        if not line.startswith(b"data:"):
            return
        payload = line[len(b"data:"):].strip()
        if not payload or payload == b"[DONE]":
            return
        if self.ttft_ms is None:
            self.ttft_ms = _elapsed_ms(self._started)
        if payload.startswith(b'{"e2ee":'):
            # Hot path: a sealed chunk is opaque here; no need to JSON-parse it.
            self.content_chunks += 1
            return
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict):
            return
        if "e2ee" in obj:
            self.content_chunks += 1
        elif "usage" in obj and len(obj) == 1:
            usage = usage_from_e2ee(obj["usage"])
            if usage is not None:
                self.usage = usage
                self.usage_seen = True


def usage_from_e2ee(value) -> Usage | None:
    """Plaintext E2EE usage (header string or SSE dict) -> ``Usage``, strictly.

    Delegates validation to ``talaris_contracts.e2ee.parse_usage`` (non-negative
    ints only; anything else becomes None), so the billing parse is the one the
    protocol defines rather than a second, laxer one.
    """
    if isinstance(value, dict):
        value = json.dumps(value)
    if not isinstance(value, str):
        return None
    parsed = parse_usage(value)
    if parsed is None:
        return None
    prompt = parsed["prompt_tokens"] or 0
    completion = parsed["completion_tokens"] or 0
    total = parsed["total_tokens"] or (prompt + completion)
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
