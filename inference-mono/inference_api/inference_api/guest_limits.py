"""Anonymous-inference abuse limits (HYP-67).

Two independent, server-enforced Redis counters gate every unauthenticated
``POST /v1/guest/inference`` call:

* ``guest:client:{sha256(client_id)}:{utc_date}`` -- per browser, keyed off the
  caller-supplied ``X-Guest-Id`` header (a UUID the frontend mints once and
  stores in ``localStorage``). This is a SOFT signal only: clearing
  localStorage, opening a private window, or simply sending a fresh UUID
  resets it instantly. It exists to stop the common case (one browser looping
  the free sandbox) -- it is deliberately NOT relied on as the abuse backstop.
* ``guest:ip:{sha256(ip)}:{utc_date}`` -- per resolved client IP
  (``inference_api.client_ip.resolve_client_ip``), shared across every
  browser/client_id behind that IP. THIS is the real backstop: an attacker who
  rotates ``client_id`` (or clears localStorage) every request still cannot
  exceed ``settings.guest_ip_daily_limit`` completions per IP per day. It is
  still bypassable by rotating source IPs (residential proxies, mobile
  networks) and it deliberately trades off against shared-IP false positives
  (many legitimate users behind one NAT/office IP sharing one ceiling) -- see
  HYP-67's ticket note on this tradeoff. Both checks must pass; there is no way
  to satisfy one and skip the other.

Both counters are incremented on every call, INCLUDING one that is ultimately
rejected -- mirrors ``inference_api.api_keys.APIKeyService.enforce_rate_limit``'s
own increment-then-check pattern (the request that trips the cap is itself
counted). A client that ignores its 429s and keeps retrying past its own
per-client cap only speeds up exhausting the SHARED per-IP ceiling faster,
which is the desired direction for an abuser, not a bug.

Window: a fixed UTC-calendar-day bucket (the key embeds ``YYYYMMDD``), not a
true sliding 24h window -- simpler and exactly what the HYP-67 ticket's own
example key format (``guest:ip:{ip_hash}:{date}``) specifies. In the worst
case (a request at 23:59:59 UTC) this gives a caller slightly less than a full
24h before the counter resets, never more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import redis.asyncio as redis

from inference_api.security import keyed_hash, utcnow

# 25 hours: a full UTC calendar day plus slack, so a key written a few seconds
# before midnight UTC can't expire mid-window on Redis's own clock.
_COUNTER_TTL_SECONDS = 25 * 60 * 60

CLIENT_LIMIT_EXCEEDED = "client_daily_limit_exceeded"
IP_LIMIT_EXCEEDED = "ip_daily_limit_exceeded"


def _date_bucket(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _client_key(client_id: str, now: datetime) -> str:
    return f"guest:client:{keyed_hash(client_id)}:{_date_bucket(now)}"


def _ip_key(ip: str, now: datetime) -> str:
    return f"guest:ip:{keyed_hash(ip)}:{_date_bucket(now)}"


@dataclass(frozen=True)
class GuestLimitResult:
    allowed: bool
    reason: str | None  # one of CLIENT_LIMIT_EXCEEDED / IP_LIMIT_EXCEEDED / None
    client_count: int
    client_limit: int
    ip_count: int
    ip_limit: int

    @property
    def client_remaining(self) -> int:
        return max(0, self.client_limit - self.client_count)

    @property
    def ip_remaining(self) -> int:
        return max(0, self.ip_limit - self.ip_count)


async def _incr_with_expiry(redis_client: redis.Redis, key: str) -> int:
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, _COUNTER_TTL_SECONDS)
    return count


async def check_and_increment_guest_limits(
    redis_client: redis.Redis,
    *,
    client_id: str,
    ip: str,
    client_limit: int,
    ip_limit: int,
    now: datetime | None = None,
) -> GuestLimitResult:
    """Atomically increment BOTH counters, then report whether either cap was hit.

    ``now`` is accepted as a parameter (rather than read internally in every
    branch) so tests can inject a specific instant to exercise day-boundary /
    window-reset behavior without sleeping or mocking a module-global clock.
    """
    now = now or utcnow()
    client_count = await _incr_with_expiry(redis_client, _client_key(client_id, now))
    ip_count = await _incr_with_expiry(redis_client, _ip_key(ip, now))

    reason = None
    if client_count > client_limit:
        reason = CLIENT_LIMIT_EXCEEDED
    elif ip_count > ip_limit:
        reason = IP_LIMIT_EXCEEDED

    return GuestLimitResult(
        allowed=reason is None,
        reason=reason,
        client_count=client_count,
        client_limit=client_limit,
        ip_count=ip_count,
        ip_limit=ip_limit,
    )
