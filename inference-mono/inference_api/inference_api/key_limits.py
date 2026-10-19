"""Per-API-key restrictions enforced on the inference hot path (HYP-45).

* IP allowlist -- ``check_ip_allowed``, called from ``api_keys.current_api_key``
  against the trusted-proxy-resolved client IP (``client_ip.resolve_client_ip``).
* Spend caps   -- ``enforce_spend_limits``, called from
  ``UsageService.reserve_inference_tokens`` after the reservation has been
  priced and split between the period allowance and the pay-as-you-go wallet,
  and while the user's usage-period row is locked (so concurrent reservations
  for the same user serialize and can't jointly overshoot a cap).

Spend is read straight off the billing ledger (``inference_usage_events``):

* credits used = ``charged_tokens`` of settled events + ``reserved_tokens`` of
  still-live reservations, from any funding source;
* USD used     = the wallet-funded slice (``wallet_reserved_tokens``) of the
  same, at ``settings.api_key_usd_cents_per_million_credits``.

A reservation is rejected if it would take the key past either cap -- the
reservation is an upper bound (prompt + ``max_tokens``), so a caller near its
cap can still get through by asking for fewer tokens. The control plane's
``app/api_keys/limits.py`` is a read-only mirror of these windows/sums for the
dashboard; keep the two in sync.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.models import APIKey, InferenceUsageEvent
from inference_api.security import utcnow

LIMIT_PERIODS = ("daily", "monthly", "never")


def ip_in_allowlist(client_ip: str | None, allowed: list[str]) -> bool:
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    for entry in allowed:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except (ValueError, TypeError):
            continue
    return False


def check_ip_allowed(db_key: APIKey, client_ip: str | None) -> None:
    """403 unless ``client_ip`` matches the key's optional allowlist.

    No-op for an unrestricted key (``allowed_ips`` NULL/empty). Fails closed
    when the caller's IP is unknown (``None``), e.g. on the router plane where
    the request comes from a subnet router rather than the end client.
    """
    allowed = db_key.allowed_ips
    if not allowed:
        return
    if not ip_in_allowlist(client_ip, allowed):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ip_not_allowed",
                "message": (
                    f"This API key is not permitted to be used from {client_ip}"
                    if client_ip
                    else "This API key is IP-restricted and the client IP could not be determined"
                ),
            },
        )


def limit_window(period: str | None, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """``(period_start, resets_at)`` in UTC. ``never`` = lifetime, no reset."""
    current = (now or utcnow()).astimezone(UTC)
    if period == "daily":
        start = datetime(current.year, current.month, current.day, tzinfo=UTC)
        return start, start + timedelta(days=1)
    if period == "monthly":
        start = datetime(current.year, current.month, 1, tzinfo=UTC)
        if current.month == 12:
            return start, datetime(current.year + 1, 1, 1, tzinfo=UTC)
        return start, datetime(current.year, current.month + 1, 1, tzinfo=UTC)
    return None, None


def wallet_tokens_to_usd_cents(tokens: int) -> Decimal:
    return Decimal(tokens) * Decimal(settings.api_key_usd_cents_per_million_credits) / Decimal(1_000_000)


async def key_spend(session: AsyncSession, api_key_id: uuid.UUID, period_start: datetime | None) -> tuple[int, int]:
    """``(credits_used, wallet_tokens_used)`` for a key since ``period_start``."""
    now = utcnow()
    live_reservation = and_(InferenceUsageEvent.status == "reserved", InferenceUsageEvent.expires_at > now)
    credits = case(
        (InferenceUsageEvent.status == "settled", InferenceUsageEvent.charged_tokens),
        (live_reservation, InferenceUsageEvent.reserved_tokens),
        else_=0,
    )
    wallet = case(
        (InferenceUsageEvent.status == "settled", InferenceUsageEvent.wallet_reserved_tokens),
        (live_reservation, InferenceUsageEvent.wallet_reserved_tokens),
        else_=0,
    )
    statement = select(func.coalesce(func.sum(credits), 0), func.coalesce(func.sum(wallet), 0)).where(
        InferenceUsageEvent.api_key_id == api_key_id
    )
    if period_start is not None:
        statement = statement.where(InferenceUsageEvent.created_at >= period_start)
    credits_used, wallet_used = (await session.execute(statement)).one()
    return int(credits_used), int(wallet_used)


def _limit_exceeded(code: str, message: str, resets_at: datetime | None) -> HTTPException:
    detail: dict[str, object] = {"code": code, "message": message}
    if resets_at is not None:
        detail["resets_at"] = resets_at.isoformat()
    return HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)


async def enforce_spend_limits(
    session: AsyncSession,
    api_key_id: uuid.UUID | None,
    *,
    additional_credits: int,
    additional_wallet_tokens: int,
) -> None:
    """402 if this reservation would take the key past its credit or USD cap."""
    if api_key_id is None:
        return
    db_key = await session.get(APIKey, api_key_id)
    if db_key is None or (db_key.credit_limit is None and db_key.usd_limit_cents is None):
        return
    period_start, resets_at = limit_window(db_key.limit_period)
    window = {"daily": "today", "monthly": "this month"}.get(db_key.limit_period or "", "in total")
    resets = f" It resets at {resets_at.isoformat()}." if resets_at is not None else ""
    credits_used, wallet_used = await key_spend(session, api_key_id, period_start)

    if db_key.credit_limit is not None and credits_used + additional_credits > db_key.credit_limit:
        remaining = max(0, db_key.credit_limit - credits_used)
        raise _limit_exceeded(
            "api_key_credit_limit_exceeded",
            f"This request needs up to {additional_credits:,} credits but this API key has "
            f"{remaining:,} of its {db_key.credit_limit:,}-credit limit left {window}.{resets} "
            "Lower max_tokens, or raise the key's limit in the dashboard.",
            resets_at,
        )
    if db_key.usd_limit_cents is not None and additional_wallet_tokens > 0:
        projected = wallet_tokens_to_usd_cents(wallet_used + additional_wallet_tokens)
        if projected > db_key.usd_limit_cents:
            used = wallet_tokens_to_usd_cents(wallet_used).to_integral_value(rounding=ROUND_CEILING)
            raise _limit_exceeded(
                "api_key_usd_limit_exceeded",
                f"This request would take this API key's pay-as-you-go spend past its "
                f"${db_key.usd_limit_cents / 100:,.2f} limit {window} "
                f"(${int(used) / 100:,.2f} used).{resets} "
                "Lower max_tokens, or raise the key's limit in the dashboard.",
                resets_at,
            )
