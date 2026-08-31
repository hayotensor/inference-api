"""Miner service-client auth dependency.

Mirrors ``inference_api.service_clients.current_router_client`` exactly, but
requires ``ServiceClientRole.miner``. This is the FIRST of the two-layer
registration auth (the second layer is the hotkey self-registration signature,
verified ONLY via ``talaris_contracts.verify_registration_signature``).

``current_miner_client`` only proves "this bearer holds SOME valid miner-role
service token" — it says nothing about WHICH miner the caller may act as.
Every miner-scoped route beyond ``/register`` (heartbeat, ``/me``, ...) MUST
additionally call ``require_bound_miner`` below, or a miner-role token that
never registered anything can read or mutate an unrelated miner's state.

## Ownership binding: mechanism + tradeoff

``/miners/register`` already has the real proof of identity: the hotkey
Ed25519/sr25519 self-registration signature, verified via
``talaris_contracts.verify_registration_signature``. Rather than requiring a
fresh signature on every heartbeat/`/me` call (which the already-deployed
heartbeat daemon does not send, and — being a different repo — this fix
cannot add), we bind the AUTHENTICATED service-client identity to the miner it
just proved ownership of, and enforce that binding on every subsequent
miner-scoped call from that client:

  - ``bind_client_to_miner`` is called by the ``/register`` route right after
    a registration commits, recording ``miner_id -> client_id`` (the miner is
    the key so a credential ROTATION — same hotkey, new service token —
    correctly replaces the previous owner rather than leaving both valid).
  - ``require_bound_miner`` is called by every OTHER miner-scoped route,
    rejecting the call unless the caller's ``client_id`` is the CURRENT owner
    recorded for the ``miner_id`` (or hotkey-derived miner) it is trying to
    read/write.

The binding is stored in Redis (``bind_client_to_miner``/``owner_client_id``
below), not a new Postgres column — this ships as a targeted fix without a
schema migration, mirroring the registration nonce replay guard
(``miners.replay``) which already leans on Redis for miner-related auth
state. The real tradeoff: Redis here is not guaranteed as durable as
Postgres, so a Redis data loss (flush/restart without persistence) forgets
every binding and every miner must re-register (send a fresh signed
``/register``) before heartbeat/`/me` work again — re-registration is cheap
and already idempotent (``upsert_from_registration``), and a currently-running
heartbeat daemon that predates this fix has, by construction, no binding
recorded yet either, so it MUST re-register once after this change deploys.
A signature-per-heartbeat design would avoid the Redis dependency but was
rejected here because it requires the (separate-repo) miner client to start
signing heartbeats, which cannot be done from this side of the fix.
"""

from __future__ import annotations

import redis.asyncio as redis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.db import get_async_session
from inference_api.errors import unauthorized
from inference_api.models import ServiceClient, ServiceClientRole
from inference_api.redis import get_redis
from inference_api.service_clients import ServiceClientService

miner_client_bearer = HTTPBearer(auto_error=False)

_OWNER_KEY_PREFIX = "miner-owner-client:"


def _owner_key(miner_id: object) -> str:
    return f"{_OWNER_KEY_PREFIX}{miner_id}"


async def bind_client_to_miner(
    redis_client: redis.Redis, client_id: object, miner_id: object
) -> None:
    """Record that ``client_id`` (an already hotkey-signature-verified
    registration) is the CURRENT owner of ``miner_id`` for subsequent
    miner-scoped calls.

    Keyed by ``miner_id -> client_id`` (single canonical owner per miner),
    not the other way around, so re-registering the same hotkey under a
    DIFFERENT service token (credential rotation) correctly replaces the
    previous owner rather than leaving both tokens valid forever — the
    signature, not the service token, is the root of trust for WHO the miner
    is, and only the most recent signature-verified registration should carry
    that authority. (A single token that has registered several different
    hotkeys is still fine: each miner_id gets its own key, all pointing at
    that same client_id.)
    """
    await redis_client.set(_owner_key(miner_id), str(client_id))


async def owner_client_id(redis_client: redis.Redis, miner_id: object) -> str | None:
    """The client_id currently authorized to act as ``miner_id``, or ``None``."""
    return await redis_client.get(_owner_key(miner_id))


async def require_bound_miner(
    redis_client: redis.Redis, *, client_id: object, miner_id: object
) -> None:
    """Raise 401 unless ``client_id`` is the current owner of ``miner_id``.

    Same "Unknown miner" message whether there is simply no recorded owner yet
    (never registered / pre-fix token) or the owner is a DIFFERENT client
    (someone else's token) — a uniform message avoids confirming to an
    attacker that the target miner_id/hotkey exists at all.
    """
    owner = await owner_client_id(redis_client, miner_id)
    if owner is None or owner != str(client_id):
        raise unauthorized("Unknown miner")


async def raw_miner_token_from_request(
    credentials: HTTPAuthorizationCredentials | None = Depends(miner_client_bearer),
) -> str:
    if (
        credentials
        and credentials.scheme.lower() == "bearer"
        and credentials.credentials.startswith("rk_")
    ):
        return credentials.credentials
    raise unauthorized("Miner service token required")


async def current_miner_client(
    request: Request,
    raw_token: str = Depends(raw_miner_token_from_request),
    session: AsyncSession = Depends(get_async_session),
    redis_client: redis.Redis = Depends(get_redis),
) -> ServiceClient:
    client = await ServiceClientService(session).authenticate_token(
        raw_token,
        redis_client,
        required_role=ServiceClientRole.miner,
    )
    request.state.service_client = client
    return client
