"""Thin TEE HTTP client (sync + async) over the enclave's wire endpoints.

Endpoints mirrored from the TEE container routers:
- ``GET  /attestation?nonce=<hex>`` -> AttestationDocument
- ``POST /provision``               -> {"provisioned": true}
- ``POST /bind``                     -> {"bound": true, "miner": ...}
- ``POST /v1/chat/completions``      -> OpenAI-shaped response (Bearer auth)
- ``GET  /stats``                    -> {"aggregate": {...}, "usage_chain_head": ...}

A pre-built httpx client/transport may be injected so a TLS-pinned client from
the verifier package can be supplied. Bearer/admin tokens are NEVER logged or
placed in any error/repr; they only ever go into the ``Authorization`` header.
"""

from __future__ import annotations

from typing import Any

import httpx

from .attestation import AttestationDocument
from .bind import BindRequest
from .crypto import build_provision_request
from .provision import Credentials, ProvisionRequest

__all__ = ["TeeClient", "AsyncTeeClient"]


def _attestation_params(nonce: bytes) -> dict[str, str]:
    """Build the ``?nonce=<hex>`` query for GET /attestation."""
    return {"nonce": nonce.hex()}


def _bearer(token: str) -> dict[str, str]:
    """Build an Authorization: Bearer header. Token is never logged."""
    return {"Authorization": f"Bearer {token}"}


def _provision_body(
    creds: Credentials | dict[str, Any] | ProvisionRequest,
    enclave_sealing_pub_hex: str | None,
    platform_signing_key: Any,
) -> dict[str, Any]:
    """Build the POST /provision JSON body.

    Accepts either a ready ``ProvisionRequest`` (used verbatim) or raw creds plus
    the enclave sealing pubkey and a platform SigningKey to seal+sign on the fly.
    """
    if isinstance(creds, ProvisionRequest):
        return creds.model_dump()
    if enclave_sealing_pub_hex is None or platform_signing_key is None:
        raise ValueError(
            "provision() needs enclave_sealing_pub_hex and platform_signing_key "
            "when creds is not a prebuilt ProvisionRequest"
        )
    req = build_provision_request(
        creds, enclave_sealing_pub_hex, platform_signing_key
    )
    return req.model_dump()


class TeeClient:
    """Synchronous TEE HTTP client.

    Either pass a ``base_url`` (a default httpx.Client is created and owned) or
    inject a pre-built ``client`` (e.g. a TLS-pinned client from the verifier
    package); an injected client is NOT closed by this wrapper. A custom
    ``transport`` (e.g. httpx.MockTransport) may be supplied with a ``base_url``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float | httpx.Timeout = 30.0,
    ) -> None:
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            if base_url is None:
                raise ValueError("either base_url or client must be provided")
            self._client = httpx.Client(
                base_url=base_url, transport=transport, timeout=timeout
            )
            self._owns_client = True

    # -- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TeeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- endpoints ----------------------------------------------------------
    def get_attestation(self, nonce: bytes) -> AttestationDocument:
        """GET /attestation?nonce=<hex> -> parsed AttestationDocument."""
        resp = self._client.get("/attestation", params=_attestation_params(nonce))
        resp.raise_for_status()
        return AttestationDocument.model_validate(resp.json())

    def provision(
        self,
        creds: Credentials | dict[str, Any] | ProvisionRequest,
        enclave_sealing_pub_hex: str | None = None,
        platform_signing_key: Any = None,
    ) -> dict[str, Any]:
        """POST /provision. Seals+signs creds (or sends a prebuilt request)."""
        body = _provision_body(creds, enclave_sealing_pub_hex, platform_signing_key)
        resp = self._client.post("/provision", json=body)
        resp.raise_for_status()
        return resp.json()

    def bind(self, req: BindRequest) -> dict[str, Any]:
        """POST /bind -> {"bound": true, "miner": ...}."""
        resp = self._client.post("/bind", json=req.model_dump())
        resp.raise_for_status()
        return resp.json()

    def chat_completions(
        self, token: str, payload: dict[str, Any], *, stream: bool = False
    ) -> httpx.Response:
        """POST /v1/chat/completions with Bearer auth.

        Returns the raw httpx.Response (the caller handles JSON vs. SSE stream).
        The bearer token is placed only in the Authorization header, never logged.
        """
        headers = _bearer(token)
        if stream:
            req = self._client.build_request(
                "POST", "/v1/chat/completions", json=payload, headers=headers
            )
            return self._client.send(req, stream=True)
        return self._client.post(
            "/v1/chat/completions", json=payload, headers=headers
        )

    def stats(self, admin_token: str) -> dict[str, Any]:
        """GET /stats with admin Bearer auth -> usage aggregate + chain head."""
        resp = self._client.get("/stats", headers=_bearer(admin_token))
        resp.raise_for_status()
        return resp.json()


class AsyncTeeClient:
    """Asynchronous TEE HTTP client (mirror of :class:`TeeClient`)."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | httpx.Timeout = 30.0,
    ) -> None:
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            if base_url is None:
                raise ValueError("either base_url or client must be provided")
            self._client = httpx.AsyncClient(
                base_url=base_url, transport=transport, timeout=timeout
            )
            self._owns_client = True

    # -- lifecycle ----------------------------------------------------------
    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncTeeClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- endpoints ----------------------------------------------------------
    async def get_attestation(self, nonce: bytes) -> AttestationDocument:
        resp = await self._client.get(
            "/attestation", params=_attestation_params(nonce)
        )
        resp.raise_for_status()
        return AttestationDocument.model_validate(resp.json())

    async def provision(
        self,
        creds: Credentials | dict[str, Any] | ProvisionRequest,
        enclave_sealing_pub_hex: str | None = None,
        platform_signing_key: Any = None,
    ) -> dict[str, Any]:
        body = _provision_body(creds, enclave_sealing_pub_hex, platform_signing_key)
        resp = await self._client.post("/provision", json=body)
        resp.raise_for_status()
        return resp.json()

    async def bind(self, req: BindRequest) -> dict[str, Any]:
        resp = await self._client.post("/bind", json=req.model_dump())
        resp.raise_for_status()
        return resp.json()

    async def chat_completions(
        self, token: str, payload: dict[str, Any], *, stream: bool = False
    ) -> httpx.Response:
        headers = _bearer(token)
        if stream:
            req = self._client.build_request(
                "POST", "/v1/chat/completions", json=payload, headers=headers
            )
            return await self._client.send(req, stream=True)
        return await self._client.post(
            "/v1/chat/completions", json=payload, headers=headers
        )

    async def stats(self, admin_token: str) -> dict[str, Any]:
        resp = await self._client.get("/stats", headers=_bearer(admin_token))
        resp.raise_for_status()
        return resp.json()
