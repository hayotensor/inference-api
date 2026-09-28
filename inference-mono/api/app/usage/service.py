from __future__ import annotations

import calendar
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import seed_default_billing_plans
from app.core.config import settings
from app.core.errors import bad_request, payment_required
from app.core.logging import request_id_ctx
from app.core.security import as_utc, expires_in, utcnow
from app.db.models.billing_plan import BillingPlan
from app.db.models.crypto_balance_snapshot import CryptoBalanceSnapshot
from app.db.models.inference_usage_event import InferenceUsageEvent
from app.db.models.manual_token_adjustment import ManualTokenAdjustment
from app.db.models.usage_period import UsagePeriod
from app.db.models.user import User
from app.db.models.user_subscription import UserSubscription
from app.db.models.user_wallet import UserWallet
from app.db.models.wallet_transaction import WalletTransaction
from app.usage.pricing import ModelPricingService

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def charged_token_count(raw_tokens: int, multiplier: Decimal) -> int:
    return int((Decimal(raw_tokens) * multiplier).to_integral_value(rounding=ROUND_CEILING))


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _previous_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _month_anchor(now: datetime, day: int, *, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    max_day = calendar.monthrange(now.year, now.month)[1]
    return datetime(now.year, now.month, min(day, max_day), hour, minute, second, tzinfo=UTC)


def _account_creation_period(created_at: datetime, now: datetime) -> tuple[datetime, datetime]:
    created = as_utc(created_at)
    current = as_utc(now)
    start = _month_anchor(
        current,
        created.day,
        hour=created.hour,
        minute=created.minute,
        second=created.second,
    )
    if start > current:
        year, month = _previous_month(start.year, start.month)
        previous_context = datetime(year, month, 1, tzinfo=UTC)
        start = _month_anchor(
            previous_context,
            created.day,
            hour=created.hour,
            minute=created.minute,
            second=created.second,
        )
    year, month = _next_month(start.year, start.month)
    end_context = datetime(year, month, 1, tzinfo=UTC)
    end = _month_anchor(
        end_context,
        created.day,
        hour=created.hour,
        minute=created.minute,
        second=created.second,
    )
    return start, end


def _calendar_month_period(now: datetime) -> tuple[datetime, datetime]:
    current = as_utc(now)
    start = _month_anchor(current, settings.token_reset_day)
    if start > current:
        year, month = _previous_month(start.year, start.month)
        start = _month_anchor(datetime(year, month, 1, tzinfo=UTC), settings.token_reset_day)
    year, month = _next_month(start.year, start.month)
    end = _month_anchor(datetime(year, month, 1, tzinfo=UTC), settings.token_reset_day)
    return start, end


def _weekly_period(now: datetime) -> tuple[datetime, datetime]:
    """7-day window anchored at midnight UTC of the most recent token_reset_weekday."""
    current = as_utc(now)
    midnight = datetime(current.year, current.month, current.day, tzinfo=UTC)
    days_since_anchor = (midnight.weekday() - settings.token_reset_weekday) % 7
    start = midnight - timedelta(days=days_since_anchor)
    return start, start + timedelta(days=7)


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current_period(self, user: User) -> UsagePeriod:
        return await self.recalculate_current_period(user)

    async def allowance(self, user: User) -> dict[str, int | datetime]:
        period = await self.recalculate_current_period(user)
        return {
            "period_start": period.period_start,
            "period_end": period.period_end,
            "stripe_monthly_allowance": period.stripe_allowance,
            "ethereum_erc20_balance_allowance": period.ethereum_erc20_allowance,
            "substrate_evm_native_balance_allowance": period.substrate_native_allowance,
            "subnet_stake_allowance": period.subnet_stake_allowance,
            "manual_adjustments": period.manual_allowance,
            "total_monthly_allowance": period.total_allowance,
            "used_tokens": period.used_tokens,
            "remaining_tokens": period.remaining_tokens,
        }

    async def usage_history(self, user: User, *, limit: int = 100) -> list[InferenceUsageEvent]:
        result = await self.session.execute(
            select(InferenceUsageEvent)
            .where(InferenceUsageEvent.user_id == user.id)
            .order_by(InferenceUsageEvent.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def usage_by_model(
        self, user: User, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict], int]:
        """Per-model totals across all of the user's usage events (not period-scoped,
        mirroring usage_history), for the per-model usage table. Returns
        (rows for the requested page, total number of distinct models)."""
        counted_tokens = self._counted_tokens_expr()
        grouped = (
            select(InferenceUsageEvent.model)
            .where(InferenceUsageEvent.user_id == user.id)
            .group_by(InferenceUsageEvent.model)
        )
        total = await self._count(grouped)

        result = await self.session.execute(
            select(
                InferenceUsageEvent.model,
                func.count(InferenceUsageEvent.id),
                func.coalesce(func.sum(InferenceUsageEvent.prompt_tokens), 0),
                func.coalesce(func.sum(InferenceUsageEvent.completion_tokens), 0),
                func.coalesce(func.sum(counted_tokens), 0),
                func.max(InferenceUsageEvent.created_at),
            )
            .where(InferenceUsageEvent.user_id == user.id)
            .group_by(InferenceUsageEvent.model)
            .order_by(func.coalesce(func.sum(counted_tokens), 0).desc(), InferenceUsageEvent.model.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = [
            {
                "model": model,
                "request_count": request_count,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "charged_tokens": charged_tokens,
                "last_used_at": last_used_at,
            }
            for (
                model,
                request_count,
                prompt_tokens,
                completion_tokens,
                charged_tokens,
                last_used_at,
            ) in result.all()
        ]
        return rows, total

    async def usage_by_model_detail(
        self, user: User, model: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[dict, list[InferenceUsageEvent]] | None:
        """Totals plus a page of the most recent events for a single model. Returns
        None if the user has no usage events for that model at all. The summary
        always reflects the model's full totals, independent of the events page."""
        counted_tokens = self._counted_tokens_expr()
        summary_result = await self.session.execute(
            select(
                func.count(InferenceUsageEvent.id),
                func.coalesce(func.sum(InferenceUsageEvent.prompt_tokens), 0),
                func.coalesce(func.sum(InferenceUsageEvent.completion_tokens), 0),
                func.coalesce(func.sum(counted_tokens), 0),
                func.max(InferenceUsageEvent.created_at),
            ).where(
                InferenceUsageEvent.user_id == user.id,
                InferenceUsageEvent.model == model,
            )
        )
        request_count, prompt_tokens, completion_tokens, charged_tokens, last_used_at = (
            summary_result.one()
        )
        if request_count == 0:
            return None
        events_result = await self.session.execute(
            select(InferenceUsageEvent)
            .where(
                InferenceUsageEvent.user_id == user.id,
                InferenceUsageEvent.model == model,
            )
            .order_by(InferenceUsageEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        summary = {
            "model": model,
            "request_count": request_count,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "charged_tokens": charged_tokens,
            "last_used_at": last_used_at,
        }
        # request_count is already the total matching-row count for this model
        # (unaffected by the events page's limit/offset), so it doubles as total_events.
        return summary, list(events_result.scalars())

    async def _count(self, statement) -> int:
        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        return int((await self.session.execute(count_statement)).scalar_one())

    @staticmethod
    def _counted_tokens_expr():
        """Total tokens a live (settled, or still-reserved-and-unexpired) event
        counts for *usage reporting* -- per-model totals, history, etc. This is
        funding-source-agnostic: it includes whatever HYP-66 drew from the wallet
        as well as the period allowance, because both are real usage. Contrast
        with `_period_allowance_tokens_expr`, which only the period's own
        used_tokens/remaining_tokens bookkeeping should use."""
        now = utcnow()
        return case(
            (InferenceUsageEvent.status == "settled", InferenceUsageEvent.charged_tokens),
            (
                and_(
                    InferenceUsageEvent.status == "reserved",
                    InferenceUsageEvent.expires_at > now,
                ),
                InferenceUsageEvent.reserved_tokens,
            ),
            else_=0,
        )

    @staticmethod
    def _period_allowance_tokens_expr():
        """The allowance-funded slice of `_counted_tokens_expr` -- excludes
        whatever HYP-66 drew from the wallet instead. This is what
        UsagePeriod.used_tokens/remaining_tokens must reconcile to; the wallet
        side of the split lives on UserWallet.balance_tokens instead."""
        now = utcnow()
        return case(
            (
                InferenceUsageEvent.status == "settled",
                InferenceUsageEvent.charged_tokens - InferenceUsageEvent.wallet_reserved_tokens,
            ),
            (
                and_(
                    InferenceUsageEvent.status == "reserved",
                    InferenceUsageEvent.expires_at > now,
                ),
                InferenceUsageEvent.reserved_tokens - InferenceUsageEvent.wallet_reserved_tokens,
            ),
            else_=0,
        )

    async def expire_stale_reservations(self, user_id: uuid.UUID | None = None) -> int:
        now = utcnow()
        statement = select(InferenceUsageEvent).where(
            InferenceUsageEvent.status == "reserved",
            InferenceUsageEvent.expires_at <= now,
        ).with_for_update()
        if user_id is not None:
            statement = statement.where(InferenceUsageEvent.user_id == user_id)
        result = await self.session.execute(statement)
        expired = 0
        for event in result.scalars():
            period = await self._lock_period(event.usage_period_id)
            period_portion = event.reserved_tokens - event.wallet_reserved_tokens
            period.used_tokens = max(0, period.used_tokens - period_portion)
            period.remaining_tokens = max(0, period.total_allowance - period.used_tokens)
            to_add = [period, event]
            if event.wallet_reserved_tokens:
                to_add.extend(await self._refund_wallet(event.user_id, event.wallet_reserved_tokens))
            event.status = "expired"
            event.released_at = now
            self.session.add_all(to_add)
            expired += 1
        if expired:
            await self.session.flush()
        return expired

    async def reserve_inference_tokens(
        self,
        user: User,
        *,
        api_key_id: uuid.UUID,
        router_client_id: uuid.UUID | None = None,
        model: str,
        prompt_tokens: int,
        max_completion_tokens: int,
        request_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> InferenceUsageEvent:
        request_id = request_id or request_id_ctx.get() or str(uuid.uuid4())
        await self.expire_stale_reservations(user.id)
        existing = (
            await self.session.execute(
                select(InferenceUsageEvent).where(
                    InferenceUsageEvent.user_id == user.id,
                    InferenceUsageEvent.request_id == request_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        period = await self.recalculate_current_period(user)
        locked_period = await self._lock_period(period.id)
        existing = (
            await self.session.execute(
                select(InferenceUsageEvent).where(
                    InferenceUsageEvent.user_id == user.id,
                    InferenceUsageEvent.request_id == request_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        multiplier = await ModelPricingService(self.session).multiplier_for_model(model)
        estimated_raw_tokens = prompt_tokens + max_completion_tokens
        reserved_tokens = charged_token_count(estimated_raw_tokens, multiplier)

        # HYP-66: once the subscription allowance for the period is exhausted,
        # draw the rest from the pay-as-you-go wallet instead of 402ing outright.
        # Only the portion beyond the remaining allowance ever touches the wallet.
        allowance_portion = min(reserved_tokens, locked_period.remaining_tokens)
        wallet_portion = reserved_tokens - allowance_portion
        wallet: UserWallet | None = None
        if wallet_portion > 0:
            wallet = await self._lock_or_create_wallet(user.id)
            if wallet.balance_tokens < wallet_portion:
                raise payment_required("Insufficient inference token balance")

        event = InferenceUsageEvent(
            usage_period_id=locked_period.id,
            user_id=user.id,
            api_key_id=api_key_id,
            router_client_id=router_client_id,
            request_id=request_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            raw_total_tokens=estimated_raw_tokens,
            model_multiplier=multiplier,
            reserved_tokens=reserved_tokens,
            wallet_reserved_tokens=wallet_portion,
            charged_tokens=0,
            status="reserved",
            expires_at=expires_at or expires_in(seconds=settings.router_reservation_ttl_seconds),
        )
        locked_period.used_tokens += allowance_portion
        locked_period.remaining_tokens = max(0, locked_period.total_allowance - locked_period.used_tokens)
        to_add = [locked_period, event]
        if wallet_portion > 0:
            assert wallet is not None
            wallet.balance_tokens -= wallet_portion
            to_add.append(wallet)
            to_add.append(
                WalletTransaction(user_id=user.id, delta_tokens=-wallet_portion, kind="debit")
            )
        self.session.add_all(to_add)
        await self.session.flush()
        return event

    async def settle_inference_usage(
        self,
        event: InferenceUsageEvent,
        *,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> InferenceUsageEvent:
        if event.status == "settled":
            return event
        if event.status == "reserved" and as_utc(event.expires_at) <= utcnow():
            await self.expire_stale_reservations(event.user_id)
            raise bad_request("reservation_expired", "Inference reservation has expired")
        if event.status in {"expired", "released"}:
            raise bad_request("reservation_not_active", "Inference reservation is not active")
        period = await self._lock_period(event.usage_period_id)
        raw_total = prompt_tokens + completion_tokens
        charged_tokens = charged_token_count(raw_total, Decimal(event.model_multiplier))

        # HYP-66: re-split the final charged_tokens between the period allowance and
        # the wallet, the same way reserve_inference_tokens did -- but computed
        # against capacity with this event's *own* reservation released first, so
        # settling it neither double-counts nor double-debits what it already holds.
        old_period_portion = event.reserved_tokens - event.wallet_reserved_tokens
        old_wallet_portion = event.wallet_reserved_tokens
        available_allowance = period.remaining_tokens + old_period_portion
        new_period_portion = min(charged_tokens, available_allowance)
        new_wallet_portion = charged_tokens - new_period_portion

        wallet: UserWallet | None = None
        if new_wallet_portion > 0 or old_wallet_portion > 0:
            wallet = await self._lock_or_create_wallet(event.user_id)
        available_wallet = (wallet.balance_tokens if wallet is not None else 0) + old_wallet_portion
        if new_wallet_portion > available_wallet:
            raise payment_required("Insufficient inference token balance")

        period.used_tokens = max(0, period.used_tokens - old_period_portion + new_period_portion)
        period.remaining_tokens = max(0, period.total_allowance - period.used_tokens)
        to_add = [period, event]
        wallet_net_delta = old_wallet_portion - new_wallet_portion
        if wallet_net_delta != 0:
            assert wallet is not None
            wallet.balance_tokens += wallet_net_delta
            to_add.append(wallet)
            to_add.append(
                WalletTransaction(
                    user_id=event.user_id,
                    delta_tokens=wallet_net_delta,
                    kind="refund" if wallet_net_delta > 0 else "debit",
                )
            )
        event.prompt_tokens = prompt_tokens
        event.completion_tokens = completion_tokens
        event.raw_total_tokens = raw_total
        event.charged_tokens = charged_tokens
        event.wallet_reserved_tokens = new_wallet_portion
        event.status = "settled"
        event.settled_at = utcnow()
        self.session.add_all(to_add)
        await self.session.flush()
        return event

    async def release_inference_reservation(self, event: InferenceUsageEvent) -> None:
        if event.status != "reserved":
            return
        if as_utc(event.expires_at) <= utcnow():
            await self.expire_stale_reservations(event.user_id)
            return
        period = await self._lock_period(event.usage_period_id)
        period_portion = event.reserved_tokens - event.wallet_reserved_tokens
        period.used_tokens = max(0, period.used_tokens - period_portion)
        period.remaining_tokens = max(0, period.total_allowance - period.used_tokens)
        to_add = [period, event]
        if event.wallet_reserved_tokens:
            to_add.extend(await self._refund_wallet(event.user_id, event.wallet_reserved_tokens))
        event.status = "released"
        event.released_at = utcnow()
        self.session.add_all(to_add)
        await self.session.flush()

    async def recalculate_current_period(self, user: User) -> UsagePeriod:
        await self.expire_stale_reservations(user.id)
        await seed_default_billing_plans(self.session)
        period_start, period_end, active_subscription = await self._current_period_bounds(user)
        period = await self._get_or_create_period(user, period_start, period_end)
        stripe_allowance = await self._stripe_allowance(active_subscription)
        ethereum_allowance = await self._latest_crypto_allowance(user.id, "ethereum", "erc20")
        substrate_allowance = await self._latest_crypto_allowance(user.id, "substrate_evm", "native")
        subnet_stake_allowance = await self._latest_crypto_allowance(user.id, "hypertensor", "subnet_stake")
        manual_allowance = await self._manual_allowance(user.id, period_start, period_end)
        used_tokens = await self._period_used_tokens(period.id)
        total_allowance = (
            stripe_allowance
            + ethereum_allowance
            + substrate_allowance
            + subnet_stake_allowance
            + manual_allowance
        )
        period.stripe_allowance = stripe_allowance
        period.ethereum_erc20_allowance = ethereum_allowance
        period.substrate_native_allowance = substrate_allowance
        period.subnet_stake_allowance = subnet_stake_allowance
        period.manual_allowance = manual_allowance
        period.total_allowance = total_allowance
        period.used_tokens = used_tokens
        period.remaining_tokens = max(0, total_allowance - used_tokens)
        period.calculated_at = utcnow()
        self.session.add(period)
        await self.session.flush()
        return period

    async def _current_period_bounds(
        self, user: User
    ) -> tuple[datetime, datetime, UserSubscription | None]:
        active_subscription = await self._active_subscription(user.id)
        if (
            active_subscription is not None
            and active_subscription.current_period_start is not None
            and active_subscription.current_period_end is not None
        ):
            return (
                as_utc(active_subscription.current_period_start),
                as_utc(active_subscription.current_period_end),
                active_subscription,
            )
        if settings.token_reset_mode == "calendar_month":
            start, end = _calendar_month_period(utcnow())
        elif settings.token_reset_mode == "weekly":
            start, end = _weekly_period(utcnow())
        else:
            start, end = _account_creation_period(user.created_at, utcnow())
        # Only the period *window* falls back here. Dropping the subscription too
        # would silently bill a customer for a paid plan while granting them the
        # free allowance, whenever Stripe sends a subscription without period
        # bounds (they live on the subscription item in newer API versions).
        return start, end, active_subscription

    async def _active_subscription(self, user_id: uuid.UUID) -> UserSubscription | None:
        result = await self.session.execute(
            select(UserSubscription).where(
                UserSubscription.user_id == user_id,
                UserSubscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES),
            )
        )
        return result.scalar_one_or_none()

    async def _get_or_create_period(
        self, user: User, period_start: datetime, period_end: datetime
    ) -> UsagePeriod:
        result = await self.session.execute(
            select(UsagePeriod).where(
                UsagePeriod.user_id == user.id,
                UsagePeriod.period_start == period_start,
                UsagePeriod.period_end == period_end,
            )
        )
        period = result.scalar_one_or_none()
        if period is not None:
            return period
        period = UsagePeriod(user_id=user.id, period_start=period_start, period_end=period_end)
        self.session.add(period)
        await self.session.flush()
        return period

    async def _lock_period(self, period_id: uuid.UUID) -> UsagePeriod:
        result = await self.session.execute(
            select(UsagePeriod).where(UsagePeriod.id == period_id).with_for_update()
        )
        return result.scalar_one()

    async def _lock_or_create_wallet(self, user_id: uuid.UUID) -> UserWallet:
        """HYP-66. Mirrors app/billing/service.py's _lock_or_create_wallet (the
        wallet is credited there, from the Stripe webhook, and drawn down here)."""
        wallet = (
            await self.session.execute(
                select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if wallet is not None:
            return wallet
        wallet = UserWallet(user_id=user_id, balance_tokens=0)
        self.session.add(wallet)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
        except IntegrityError:
            wallet = (
                await self.session.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
                )
            ).scalar_one()
        return wallet

    async def _refund_wallet(self, user_id: uuid.UUID, tokens: int) -> list[Any]:
        """Credits back `tokens` to the wallet (a reservation that never turned into
        real spend -- released or expired) and returns the [wallet, transaction]
        pair for the caller to add to the session alongside its own changes."""
        wallet = await self._lock_or_create_wallet(user_id)
        wallet.balance_tokens += tokens
        transaction = WalletTransaction(user_id=user_id, delta_tokens=tokens, kind="refund")
        return [wallet, transaction]

    async def _stripe_allowance(self, active_subscription: UserSubscription | None) -> int:
        if active_subscription is not None and active_subscription.plan_id is not None:
            plan = await self.session.get(BillingPlan, active_subscription.plan_id)
            if plan is not None and plan.active:
                return plan.monthly_token_allowance
        result = await self.session.execute(
            select(BillingPlan).where(or_(BillingPlan.slug == "free", BillingPlan.name == "Free"))
        )
        free_plan = result.scalar_one_or_none()
        return free_plan.monthly_token_allowance if free_plan and free_plan.active else 0

    async def _latest_crypto_allowance(self, user_id: uuid.UUID, chain: str, token_type: str) -> int:
        result = await self.session.execute(
            select(CryptoBalanceSnapshot)
            .where(
                CryptoBalanceSnapshot.user_id == user_id,
                CryptoBalanceSnapshot.chain == chain,
                CryptoBalanceSnapshot.token_type == token_type,
                CryptoBalanceSnapshot.error_message.is_(None),
            )
            .order_by(CryptoBalanceSnapshot.checked_at.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()
        return snapshot.inference_token_allowance if snapshot is not None else 0

    async def _manual_allowance(
        self, user_id: uuid.UUID, period_start: datetime, period_end: datetime
    ) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.sum(ManualTokenAdjustment.amount), 0)).where(
                ManualTokenAdjustment.user_id == user_id,
                ManualTokenAdjustment.created_at >= period_start,
                ManualTokenAdjustment.created_at < period_end,
            )
        )
        return int(result.scalar_one())

    async def _period_used_tokens(self, usage_period_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.sum(self._period_allowance_tokens_expr()), 0)).where(
                InferenceUsageEvent.usage_period_id == usage_period_id
            )
        )
        return int(result.scalar_one())
