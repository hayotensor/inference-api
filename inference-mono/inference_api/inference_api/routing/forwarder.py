"""TEEForwarder: forward an inference request to a selected miner's enclave.

Decrypts the miner's active provisioned token, calls the enclave's
``POST /v1/chat/completions`` over a TLS-leaf-pinned client, and returns the
completion plus the REAL token usage reported by the enclave (never an estimate
from ``len(text.split())``).

Streaming is passed through unchanged while the SSE ``usage`` chunk is
accumulated so the caller can settle from real counts even if the client
disconnects mid-stream. On a ``401`` the token is re-read once and retried; on a
pin mismatch the miner is marked ``stale``.
"""

from __future__ import annotations

import json
import logging
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


class ForwardError(RuntimeError):
    """A forward attempt failed (transport, auth, pin, or upstream error)."""


class NoActiveTokenError(ForwardError):
    """The selected miner has no active provisioned token to forward with."""


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


@dataclass
class StreamForward:
    """A live streaming forward; iterate ``aiter()`` then read ``usage``."""

    candidate: MinerCandidate
    response: httpx.Response
    client: httpx.AsyncClient
    usage: Usage = field(default_factory=Usage)
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

    async def forward(self, candidate: MinerCandidate, payload: dict) -> ForwardResult:
        """Non-streaming forward. Retries once on 401 after re-reading the token."""
        for attempt in (1, 2):
            token_row, bearer = await self._active_token(candidate.miner_id, candidate.model_id)
            client = self._client(candidate, bearer)
            try:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={**payload, "stream": False},
                    headers={
                        "Authorization": f"Bearer {bearer}",
                        **({"X-Request-Id": rid} if (rid := request_id_ctx.get()) else {}),
                    },
                )
            except CertFingerprintMismatch as exc:
                await self._mark_stale(candidate.miner_id)
                raise ForwardError(f"TLS pin mismatch: {exc}") from exc
            except httpx.HTTPError as exc:
                raise ForwardError(f"forward transport error: {exc}") from exc
            finally:
                await client.aclose()

            if resp.status_code == 401 and attempt == 1:
                # Token may have rotated; re-read and retry once.
                continue
            if resp.status_code >= 400:
                raise ForwardError(
                    f"enclave returned {resp.status_code} for {candidate.hotkey[:12]}..."
                )
            body = resp.json()
            usage = Usage.from_dict(body.get("usage") if isinstance(body, dict) else None)
            return ForwardResult(
                candidate=candidate,
                status_code=resp.status_code,
                body=body,
                usage=usage,
            )
        raise ForwardError("forward failed after token re-read")

    async def forward_stream(self, candidate: MinerCandidate, payload: dict) -> StreamForward:
        """Open a streaming forward. Caller iterates and then reads ``.usage``."""
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
            headers={
                "Authorization": f"Bearer {bearer}",
                **({"X-Request-Id": rid} if (rid := request_id_ctx.get()) else {}),
            },
        )
        try:
            response = await client.send(request, stream=True)
        except CertFingerprintMismatch as exc:
            await client.aclose()
            await self._mark_stale(candidate.miner_id)
            raise ForwardError(f"TLS pin mismatch: {exc}") from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            raise ForwardError(f"forward transport error: {exc}") from exc
        if response.status_code >= 400:
            await response.aclose()
            await client.aclose()
            raise ForwardError(f"enclave returned {response.status_code}")
        return StreamForward(candidate=candidate, response=response, client=client)
