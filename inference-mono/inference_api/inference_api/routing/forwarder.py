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

from inference_api.crypto import decrypt_token
from inference_api.logging import request_id_ctx
from inference_api.models import Miner, ProvisionedToken
from inference_api.routing.pinning import build_enclave_client
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

    def _client(self, candidate: MinerCandidate, bearer: str) -> httpx.AsyncClient:
        return build_enclave_client(
            base_url=candidate.tee_endpoint,
            tls_cert_fingerprint=candidate.tls_cert_fingerprint,
            bearer=bearer,
            transport=self._transport,
        )

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
