"""Registration nonce replay guard.

A registration's ``nonce`` may be consumed at most once within its TTL. When
Redis is available we use a ``SET NX EX`` so the guard is shared across
processes; otherwise (tests, single-process) we fall back to an in-process TTL
cache. Either way, a re-used nonce is rejected.
"""

from __future__ import annotations

import time

import redis.asyncio as redis

from inference_api.config import settings


class _InProcessNonceCache:
    """Single-process TTL set of consumed nonces."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def _purge(self, now: float) -> None:
        expired = [k for k, exp in self._seen.items() if exp <= now]
        for k in expired:
            self._seen.pop(k, None)

    def claim(self, key: str, ttl: int) -> bool:
        now = time.monotonic()
        self._purge(now)
        if key in self._seen:
            return False
        self._seen[key] = now + ttl
        return True


_inprocess = _InProcessNonceCache()


async def claim_registration_nonce(
    hotkey: str, nonce: str | None, redis_client: redis.Redis | None
) -> bool:
    """Atomically claim ``(hotkey, nonce)``. Returns False if already used.

    A missing nonce is allowed through (the signature/timestamp still bind the
    payload); only a present-and-reused nonce is rejected.
    """
    if not nonce:
        return True
    ttl = settings.registration_nonce_ttl_seconds
    key = f"miner-reg-nonce:{hotkey.lower()}:{nonce}"
    if redis_client is not None:
        try:
            ok = await redis_client.set(key, "1", nx=True, ex=ttl)
            return bool(ok)
        except Exception:  # noqa: BLE001 - fall back to in-process on redis error
            pass
    return _inprocess.claim(key, ttl)
