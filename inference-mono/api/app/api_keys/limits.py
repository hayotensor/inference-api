"""Per-API-key spend accounting (HYP-45).

Read-only mirror of ``inference_api/key_limits.py`` on the serving plane,
which is what actually *enforces* a key's caps on the inference hot path. This
copy only powers the dashboard's "used this period" readout, so the two must
agree on the period windows and on what counts as spend:

* credits used = ``charged_tokens`` of settled events + ``reserved_tokens`` of
  still-live reservations (any funding source: allowance or wallet);
* USD used     = the wallet-funded slice of the same (``wallet_reserved_tokens``)
  converted at ``settings.api_key_usd_cents_per_million_credits``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import utcnow
from app.db.models.api_key import APIKey, APIKeyLimitPeriod
from app.db.models.inference_usage_event import InferenceUsageEvent


def limit_window(
    period: APIKeyLimitPeriod | str | None, now: datetime | None = None
) -> tuple[datetime | None, datetime | None]:
    """``(period_start, resets_at)`` for a key's limit period, in UTC.

    ``daily``/``monthly`` are UTC calendar windows; ``never`` is a lifetime cap
    (no start bound, never resets).
    """
    current = (now or utcnow()).astimezone(UTC)
    if period == APIKeyLimitPeriod.daily:
        start = datetime(current.year, current.month, current.day, tzinfo=UTC)
        return start, start + timedelta(days=1)
    if period == APIKeyLimitPeriod.monthly:
        start = datetime(current.year, current.month, 1, tzinfo=UTC)
        if current.month == 12:
            return start, datetime(current.year + 1, 1, 1, tzinfo=UTC)
        return start, datetime(current.year, current.month + 1, 1, tzinfo=UTC)
    return None, None


def wallet_tokens_to_usd_cents(tokens: int) -> int:
    cents = Decimal(tokens) * Decimal(settings.api_key_usd_cents_per_million_credits) / Decimal(1_000_000)
    return int(cents.to_integral_value(rounding=ROUND_CEILING))


async def key_spend(
    session: AsyncSession, api_key_id: uuid.UUID, period_start: datetime | None
) -> tuple[int, int]:
    """``(credits_used, wallet_tokens_used)`` for a key since ``period_start``."""
    now = utcnow()
    live_reservation = and_(
        InferenceUsageEvent.status == "reserved", InferenceUsageEvent.expires_at > now
    )
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
    statement = select(
        func.coalesce(func.sum(credits), 0), func.coalesce(func.sum(wallet), 0)
    ).where(InferenceUsageEvent.api_key_id == api_key_id)
    if period_start is not None:
        statement = statement.where(InferenceUsageEvent.created_at >= period_start)
    credits_used, wallet_used = (await session.execute(statement)).one()
    return int(credits_used), int(wallet_used)


async def key_spend_summary(session: AsyncSession, db_key: APIKey) -> dict:
    period_start, resets_at = limit_window(db_key.limit_period)
    credits_used, wallet_used = await key_spend(session, db_key.id, period_start)
    return {
        "api_key_id": db_key.id,
        "limit_period": db_key.limit_period,
        "period_start": period_start,
        "resets_at": resets_at,
        "credits_used": credits_used,
        "usd_cents_used": wallet_tokens_to_usd_cents(wallet_used),
        "credit_limit": db_key.credit_limit,
        "usd_limit_cents": db_key.usd_limit_cents,
    }
