from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import (
    custom_wallet_topup,
    looks_like_test_plan,
    seed_default_billing_plans,
    wallet_topup_bundle,
)
from app.billing.stripe import StripeClient
from app.core.errors import bad_request, forbidden, not_found
from app.core.security import utcnow
from app.db.models.billing_plan import BillingPlan
from app.db.models.stripe_webhook_event import StripeWebhookEvent
from app.db.models.user import User
from app.db.models.user_billing_account import UserBillingAccount
from app.db.models.user_subscription import UserSubscription
from app.db.models.user_wallet import UserWallet
from app.db.models.wallet_transaction import WalletTransaction


def _plan_rate_cents_per_token(plan: BillingPlan | None) -> Decimal | None:
    """HYP-65: a plan's effective $/token rate (its *monthly* price divided by
    its monthly token allowance, regardless of whether the referred user is
    actually billed monthly or annually -- that's the rate the ticket
    specifies). None for a plan with no defined per-token economics: no price
    (the Free plan) or no allowance ceiling at all (HYP-76's `is_unlimited`
    plans, which would otherwise divide by zero)."""
    if plan is None or plan.price_cents <= 0 or plan.monthly_token_allowance <= 0:
        return None
    return Decimal(plan.price_cents) / Decimal(plan.monthly_token_allowance)


def _obj_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _as_dict(value: Any) -> dict[str, Any]:
    """The SDK's `StripeObject` is not a dict and exposes no `keys()`/`items()`/`get()`,
    so `dict(obj)` raises KeyError instead of copying it. Real webhook payloads arrive
    as these objects; hand-built dicts (tests, internal calls) must keep working too."""
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return dict(converted)
    return {}


def _metadata(value: Any) -> dict[str, Any]:
    return _as_dict(_obj_get(value, "metadata", {}) or {})


def _stripe_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


# How long a pending Checkout Session marker is trusted without Stripe confirming
# it, if Stripe itself can't be reached to check. This is a fallback only — the
# normal path always asks Stripe for the session's live status.
_PENDING_CHECKOUT_STALE_AFTER = timedelta(minutes=30)


def _first_subscription_item(subscription: Any) -> Any | None:
    items = _obj_get(subscription, "items", {}) or {}
    data = _obj_get(items, "data", []) or []
    return data[0] if data else None


def _subscription_price_id(subscription: Any) -> str | None:
    item = _first_subscription_item(subscription)
    price = _obj_get(item, "price", {}) if item is not None else {}
    price_id = _obj_get(price, "id")
    if price_id:
        return str(price_id)
    plan = _obj_get(item, "plan", {}) if item is not None else {}
    plan_id = _obj_get(plan, "id")
    return str(plan_id) if plan_id else None


def _subscription_item_period(subscription: Any, key: str) -> datetime | None:
    value = _obj_get(subscription, key)
    if value is not None:
        return _stripe_timestamp(value)
    item = _first_subscription_item(subscription)
    return _stripe_timestamp(_obj_get(item, key)) if item is not None else None


class BillingService:
    def __init__(self, session: AsyncSession, stripe_client: StripeClient | None = None) -> None:
        self.session = session
        self.stripe_client = stripe_client or StripeClient()

    async def list_plans(self) -> list[BillingPlan]:
        await seed_default_billing_plans(self.session)
        result = await self.session.execute(
            select(BillingPlan)
            .where(BillingPlan.active.is_(True), BillingPlan.visible.is_(True))
            .order_by(BillingPlan.sort_order.asc(), BillingPlan.monthly_token_allowance.asc())
        )
        # HYP-22: filter out anything that looks like a test/e2e plan regardless of
        # its active/visible columns -- see looks_like_test_plan for why this check
        # exists on top of, not instead of, the query filter above.
        return [
            plan
            for plan in result.scalars()
            if not looks_like_test_plan(
                slug=plan.slug,
                name=plan.name,
                stripe_price_id=plan.stripe_price_id,
                stripe_annual_price_id=plan.stripe_annual_price_id,
            )
        ]

    async def _plan_and_price(self, plan_id: uuid.UUID, interval: str) -> tuple[BillingPlan, str]:
        await seed_default_billing_plans(self.session)
        plan = await self.session.get(BillingPlan, plan_id)
        if (
            plan is None
            or not plan.active
            or not plan.visible
            or looks_like_test_plan(
                slug=plan.slug,
                name=plan.name,
                stripe_price_id=plan.stripe_price_id,
                stripe_annual_price_id=plan.stripe_annual_price_id,
            )
        ):
            raise not_found("Billing plan not found")
        if interval not in ("month", "year"):
            raise bad_request("invalid_interval", "Billing interval must be 'month' or 'year'")
        price_id = plan.stripe_annual_price_id if interval == "year" else plan.stripe_price_id
        if not price_id:
            raise bad_request(
                "stripe_price_missing",
                f"Billing plan is missing a Stripe {interval}ly price ID",
            )
        return plan, price_id

    async def create_checkout_session(
        self, user: User, plan_id: uuid.UUID, interval: str = "month"
    ) -> dict[str, Any]:
        plan, price_id = await self._plan_and_price(plan_id, interval)

        active_subscription = await self._active_subscription(user.id)
        if active_subscription is not None:
            raise bad_request(
                "subscription_exists",
                "This account already has a subscription; change it from the billing dashboard",
            )

        account = await self.ensure_billing_account(user)
        await self._reject_if_checkout_pending(account)

        session = self.stripe_client.create_checkout_session(
            customer_id=account.stripe_customer_id or "",
            price_id=price_id,
            user_id=str(user.id),
            plan_id=str(plan.id),
        )
        account.pending_checkout_session_id = session["id"]
        account.pending_checkout_session_created_at = utcnow()
        self.session.add(account)
        await self.session.flush()
        return {"id": session["id"], "url": session["url"]}

    async def create_wallet_checkout_session(
        self, user: User, bundle_id: str | None = None, *, usd_cents: int | None = None
    ) -> dict[str, Any]:
        """HYP-66: a one-time (mode="payment") Checkout Session for a pay-as-you-go
        wallet top-up. Unlike subscription checkout, this deliberately does not
        call _reject_if_checkout_pending -- buying more than one bundle, or
        topping up again before a previous purchase's webhook has landed, is
        fine; nothing here creates a recurring commitment to dedupe against."""
        bundle = (
            custom_wallet_topup(usd_cents)
            if usd_cents is not None
            else wallet_topup_bundle(bundle_id or "")
        )
        if bundle is None:
            raise bad_request("unknown_wallet_bundle", "Unknown wallet top-up bundle")
        account = await self.ensure_billing_account(user)
        session = self.stripe_client.create_wallet_checkout_session(
            customer_id=account.stripe_customer_id or "",
            user_id=str(user.id),
            bundle_id=bundle.id,
            usd_cents=bundle.usd_cents,
            tokens=bundle.tokens,
        )
        return {"id": session["id"], "url": session["url"]}

    async def get_wallet_balance(self, user: User) -> int:
        wallet = (
            await self.session.execute(select(UserWallet).where(UserWallet.user_id == user.id))
        ).scalar_one_or_none()
        return wallet.balance_tokens if wallet is not None else 0

    async def _credit_wallet_topup(self, checkout_session: Any) -> None:
        """Applies a completed wallet_topup Checkout Session. Idempotency against a
        retried webhook delivery is primarily process_webhook's StripeWebhookEvent
        dedupe (by event id), but the payment_intent id is also checked here so this
        can never double-credit even if called twice for the same payment."""
        metadata = _metadata(checkout_session)
        user_id_value = metadata.get("user_id") or _obj_get(checkout_session, "client_reference_id")
        tokens_value = metadata.get("tokens")
        if not user_id_value or not tokens_value:
            return
        tokens = int(tokens_value)
        if tokens <= 0:
            return
        user_id = uuid.UUID(str(user_id_value))
        payment_intent = _obj_get(checkout_session, "payment_intent")
        payment_intent_id = str(payment_intent) if payment_intent else None

        if payment_intent_id:
            existing = (
                await self.session.execute(
                    select(WalletTransaction).where(
                        WalletTransaction.stripe_payment_intent_id == payment_intent_id,
                        WalletTransaction.kind == "purchase",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return

        wallet = await self._lock_or_create_wallet(user_id)
        wallet.balance_tokens += tokens
        self.session.add(wallet)
        self.session.add(
            WalletTransaction(
                user_id=user_id,
                delta_tokens=tokens,
                kind="purchase",
                stripe_payment_intent_id=payment_intent_id,
            )
        )
        await self.session.flush()

        # HYP-65: a wallet top-up is a real payment too, and one-time
        # (mode="payment") Checkout Sessions never produce a Stripe invoice --
        # so this is the only place a wallet top-up can trigger a referral
        # credit (contrast with _credit_referral_for_invoice, the
        # subscription-invoice.paid side). Use the price and credits recorded at
        # checkout so custom amounts receive the same referral treatment.
        usd_cents = int(metadata.get("usd_cents", "0"))
        if usd_cents > 0 and payment_intent_id:
            from app.referrals.service import ReferralService

            await ReferralService(self.session).credit_for_payment(
                referred_user_id=user_id,
                amount_cents=usd_cents,
                source_id=payment_intent_id,
                rate_cents_per_token=Decimal(usd_cents) / Decimal(tokens),
            )

    async def _lock_or_create_wallet(self, user_id: uuid.UUID) -> UserWallet:
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
            # Same race as sync_subscription_object's insert: a concurrent request
            # for the same user created the row first.
            wallet = (
                await self.session.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id).with_for_update()
                )
            ).scalar_one()
        return wallet

    async def _reject_if_checkout_pending(self, account: UserBillingAccount) -> None:
        """A Checkout Session already open for this account blocks a new one.
        Without this, a double-click, a second tab, or a retry before the webhook
        has synced the first payment could open two sessions for the same
        subscription and get the customer billed twice."""
        session_id = account.pending_checkout_session_id
        if not session_id:
            return
        try:
            existing = self.stripe_client.retrieve_checkout_session(session_id)
            status = _obj_get(existing, "status")
        except Exception:
            # Stripe unreachable/erroring — don't let that wedge checkout forever;
            # fall back to treating the marker as stale after a while.
            status = None

        if status == "open":
            raise bad_request(
                "checkout_session_pending",
                "A checkout session is already open for this account; complete or close it first",
            )
        if status in ("complete", "expired"):
            account.pending_checkout_session_id = None
            account.pending_checkout_session_created_at = None
            self.session.add(account)
            await self.session.flush()
            return

        created_at = account.pending_checkout_session_created_at
        if created_at is not None and utcnow() - created_at < _PENDING_CHECKOUT_STALE_AFTER:
            raise bad_request(
                "checkout_session_pending",
                "A checkout session is already open for this account; complete or close it first",
            )
        account.pending_checkout_session_id = None
        account.pending_checkout_session_created_at = None
        self.session.add(account)
        await self.session.flush()

    async def sync_checkout_session_by_id(self, user: User, session_id: str) -> dict[str, Any]:
        """Confirm and apply a checkout without waiting on the webhook. The success
        page calls this as soon as Stripe redirects back so the new plan shows up
        immediately; the webhook still runs and lands on the same idempotent sync,
        so a slow or duplicate delivery of it is harmless."""
        checkout_session = self.stripe_client.retrieve_checkout_session(session_id)
        metadata = _metadata(checkout_session)
        owner = metadata.get("user_id") or _obj_get(checkout_session, "client_reference_id")
        if not owner or uuid.UUID(str(owner)) != user.id:
            raise forbidden("This checkout session does not belong to your account")
        await self.sync_checkout_session(checkout_session)
        return await self.subscription_response(user)

    async def change_subscription_plan(
        self, user: User, plan_id: uuid.UUID, interval: str = "month"
    ) -> UserSubscription:
        """Move an existing subscription to another plan or interval. Stripe's customer
        portal can do this too, but only when the account has portal plan-switching
        configured — this path always works."""
        plan, price_id = await self._plan_and_price(plan_id, interval)
        subscription = await self._active_subscription(user.id)
        if subscription is None or not subscription.stripe_subscription_id:
            raise bad_request(
                "no_active_subscription",
                "There is no active subscription to change; start one from checkout",
            )
        if subscription.stripe_price_id == price_id:
            raise bad_request("already_on_plan", "The subscription is already on this plan and interval")

        stripe_subscription = self.stripe_client.retrieve_subscription(
            subscription.stripe_subscription_id
        )
        item = _first_subscription_item(stripe_subscription)
        item_id = _obj_get(item, "id") if item is not None else None
        if not item_id:
            raise bad_request("stripe_item_missing", "Stripe subscription has no line item to update")

        updated = self.stripe_client.update_subscription_price(
            subscription_id=subscription.stripe_subscription_id,
            subscription_item_id=str(item_id),
            price_id=price_id,
            user_id=str(user.id),
            plan_id=str(plan.id),
        )
        synced = await self.sync_subscription_object(updated)
        return synced or subscription

    async def create_customer_portal_session(self, user: User) -> dict[str, Any]:
        account = await self.ensure_billing_account(user)
        session = self.stripe_client.create_customer_portal_session(
            customer_id=account.stripe_customer_id or ""
        )
        return {"id": session["id"], "url": session["url"]}

    async def ensure_billing_account(self, user: User) -> UserBillingAccount:
        result = await self.session.execute(
            select(UserBillingAccount).where(UserBillingAccount.user_id == user.id)
        )
        account = result.scalar_one_or_none()
        if account is None:
            account = UserBillingAccount(user_id=user.id)
        if not account.stripe_customer_id:
            account.stripe_customer_id = self.stripe_client.create_customer(
                email=user.email,
                name=user.full_name,
                user_id=str(user.id),
            )
        self.session.add(account)
        await self.session.flush()
        return account

    async def get_subscription(self, user: User) -> UserSubscription | None:
        result = await self.session.execute(
            select(UserSubscription).where(UserSubscription.user_id == user.id)
        )
        return result.scalar_one_or_none()

    async def subscription_response(self, user: User) -> dict[str, Any]:
        subscription = await self.get_subscription(user)
        if subscription is None:
            return {"status": "free"}
        plan_name = None
        interval: str | None = None
        amount_cents: int | None = None
        currency: str | None = None
        if subscription.plan_id is not None:
            plan = await self.session.get(BillingPlan, subscription.plan_id)
            if plan is not None:
                plan_name = plan.name
                currency = plan.currency
                if subscription.stripe_price_id and subscription.stripe_price_id == plan.stripe_annual_price_id:
                    interval = "year"
                    amount_cents = plan.annual_price_cents
                elif subscription.stripe_price_id and subscription.stripe_price_id == plan.stripe_price_id:
                    interval = "month"
                    amount_cents = plan.price_cents
        return {
            "id": subscription.id,
            "stripe_subscription_id": subscription.stripe_subscription_id,
            "stripe_price_id": subscription.stripe_price_id,
            "plan_id": subscription.plan_id,
            "plan_name": plan_name,
            "interval": interval,
            "amount_cents": amount_cents,
            "currency": currency,
            "status": subscription.status,
            "current_period_start": subscription.current_period_start,
            "current_period_end": subscription.current_period_end,
            "cancel_at_period_end": subscription.cancel_at_period_end,
        }

    async def cancel_subscription(
        self, user: User, *, cancel_at_period_end: bool = True
    ) -> UserSubscription:
        subscription = await self.get_subscription(user)
        if subscription is None or not subscription.stripe_subscription_id:
            raise not_found("Stripe subscription not found")
        stripe_subscription = self.stripe_client.cancel_subscription(
            subscription_id=subscription.stripe_subscription_id,
            cancel_at_period_end=cancel_at_period_end,
        )
        synced = await self.sync_subscription_object(stripe_subscription)
        return synced or subscription

    async def process_webhook(self, payload: bytes, signature: str) -> bool:
        event = self.stripe_client.construct_webhook_event(payload=payload, signature=signature)
        event_id = str(_obj_get(event, "id"))
        event_type = str(_obj_get(event, "type"))
        stored_event = (
            await self.session.execute(
                select(StripeWebhookEvent).where(StripeWebhookEvent.stripe_event_id == event_id)
            )
        ).scalar_one_or_none()
        if stored_event is not None and stored_event.processed_at is not None:
            return False
        if stored_event is None:
            stored_event = StripeWebhookEvent(stripe_event_id=event_id, event_type=event_type)
            self.session.add(stored_event)
            await self.session.flush()

        data = _obj_get(event, "data", {}) or {}
        stripe_object = _obj_get(data, "object", {}) or {}
        if event_type == "checkout.session.completed":
            # HYP-66: a wallet top-up is a one-time payment, not a subscription --
            # it must never fall into sync_checkout_session, which expects (and
            # looks for) a `subscription` object on the session.
            if _metadata(stripe_object).get("purpose") == "wallet_topup":
                await self._credit_wallet_topup(stripe_object)
            else:
                await self.sync_checkout_session(stripe_object)
        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            await self.sync_subscription_object(stripe_object)
        elif event_type in {"invoice.paid", "invoice.payment_failed", "invoice.updated"}:
            await self.sync_invoice_subscription(stripe_object, event_type)

        stored_event.processed_at = utcnow()
        self.session.add(stored_event)
        await self.session.flush()
        return True

    async def sync_checkout_session(self, checkout_session: Any) -> None:
        metadata = _metadata(checkout_session)
        user_id = metadata.get("user_id") or _obj_get(checkout_session, "client_reference_id")
        customer_id = _obj_get(checkout_session, "customer")
        if user_id and customer_id:
            await self._upsert_billing_account(uuid.UUID(str(user_id)), str(customer_id))
        if user_id:
            await self._clear_pending_checkout(
                uuid.UUID(str(user_id)), _obj_get(checkout_session, "id")
            )
        subscription = _obj_get(checkout_session, "subscription")
        if subscription:
            if isinstance(subscription, str):
                subscription = self.stripe_client.retrieve_subscription(subscription)
            await self.sync_subscription_object(subscription)

    async def _clear_pending_checkout(self, user_id: uuid.UUID, session_id: Any) -> None:
        if not session_id:
            return
        account = (
            await self.session.execute(
                select(UserBillingAccount).where(UserBillingAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
        if account is None or account.pending_checkout_session_id != str(session_id):
            return
        account.pending_checkout_session_id = None
        account.pending_checkout_session_created_at = None
        self.session.add(account)
        await self.session.flush()

    async def sync_invoice_subscription(self, invoice: Any, event_type: str) -> None:
        subscription_id = _obj_get(invoice, "subscription")
        if not subscription_id:
            return
        try:
            subscription = self.stripe_client.retrieve_subscription(str(subscription_id))
            synced = await self.sync_subscription_object(subscription)
        except Exception:
            if event_type == "invoice.payment_failed":
                await self._mark_subscription_status(str(subscription_id), "past_due")
            return
        if event_type == "invoice.paid" and synced is not None:
            await self._credit_referral_for_invoice(invoice, synced)

    async def _credit_referral_for_invoice(self, invoice: Any, subscription: UserSubscription) -> None:
        """HYP-65: the subscription-invoice.paid side of referral crediting
        (see _credit_wallet_topup for the one-time wallet top-up side, which
        never reaches here since those don't produce invoices)."""
        invoice_id = _obj_get(invoice, "id")
        amount_paid = _obj_get(invoice, "amount_paid")
        if not invoice_id or not amount_paid:
            return
        plan = (
            await self.session.get(BillingPlan, subscription.plan_id)
            if subscription.plan_id is not None
            else None
        )
        rate = _plan_rate_cents_per_token(plan)
        if rate is None:
            # No defined per-token rate on this plan -- currently only
            # HYP-76's is_unlimited tier (see _plan_rate_cents_per_token).
            # The HYP-65 spec calls for referral credit to apply "normally"
            # to Unlimited with "no special case needed", but its allowance is
            # 0, so the plan's own price/allowance ratio is undefined rather
            # than merely large. Falling back to Business's bulk rate as the
            # reference conversion keeps referral credit flowing for an
            # Unlimited referral (matching the spec's intent) without
            # dividing by zero; see the HYP-65 report for this judgment call.
            rate = await self._fallback_referral_rate()
        if rate is None:
            return
        from app.referrals.service import ReferralService

        await ReferralService(self.session).credit_for_payment(
            referred_user_id=subscription.user_id,
            amount_cents=int(amount_paid),
            source_id=str(invoice_id),
            rate_cents_per_token=rate,
        )

    async def _fallback_referral_rate(self) -> Decimal | None:
        result = await self.session.execute(
            select(BillingPlan).where(or_(BillingPlan.slug == "business", BillingPlan.name == "Business"))
        )
        return _plan_rate_cents_per_token(result.scalar_one_or_none())

    async def sync_subscription_object(self, subscription_object: Any) -> UserSubscription | None:
        metadata = _metadata(subscription_object)
        customer_id = _obj_get(subscription_object, "customer")
        user_id_value = metadata.get("user_id")
        if not user_id_value and customer_id:
            account = (
                await self.session.execute(
                    select(UserBillingAccount).where(
                        UserBillingAccount.stripe_customer_id == str(customer_id)
                    )
                )
            ).scalar_one_or_none()
            user_id_value = str(account.user_id) if account else None
        if not user_id_value:
            return None

        user_id = uuid.UUID(str(user_id_value))
        if customer_id:
            await self._upsert_billing_account(user_id, str(customer_id))
        price_id = _subscription_price_id(subscription_object)
        plan_id = await self._plan_id_for_subscription(subscription_object, price_id)
        stripe_subscription_id = str(_obj_get(subscription_object, "id"))
        status = str(_obj_get(subscription_object, "status", "unknown"))
        if _obj_get(subscription_object, "canceled_at") and status != "canceled":
            status = "canceled"

        existing = (
            await self.session.execute(
                select(UserSubscription).where(UserSubscription.user_id == user_id)
            )
        ).scalar_one_or_none()
        is_new = existing is None
        if existing is None:
            existing = UserSubscription(user_id=user_id)
        existing.stripe_subscription_id = stripe_subscription_id
        existing.stripe_price_id = price_id
        existing.plan_id = plan_id
        existing.status = status
        existing.current_period_start = _subscription_item_period(subscription_object, "current_period_start")
        existing.current_period_end = _subscription_item_period(subscription_object, "current_period_end")
        existing.cancel_at_period_end = bool(_obj_get(subscription_object, "cancel_at_period_end", False))

        try:
            # The SAVEPOINT must open before `existing` is added — begin_nested()
            # itself autoflushes whatever is already pending as it establishes the
            # savepoint, which would run the INSERT *before* the savepoint exists
            # and poison the whole transaction instead of just this nested one.
            async with self.session.begin_nested():
                self.session.add(existing)
                await self.session.flush()
        except IntegrityError:
            if not is_new:
                raise
            # Stripe can deliver more than one event for the same subscription
            # (checkout.session.completed, customer.subscription.created, the
            # webhook racing the /billing/success sync call) as concurrent
            # requests. Both can see "no row yet" for this user and both try to
            # INSERT — the loser lands here and just needs to update the row the
            # winner committed instead of crashing the request. The failed
            # insert's object was rolled back with the savepoint, so re-fetch.
            existing = (
                await self.session.execute(
                    select(UserSubscription).where(
                        UserSubscription.stripe_subscription_id == stripe_subscription_id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            existing.stripe_price_id = price_id
            existing.plan_id = plan_id
            existing.status = status
            existing.current_period_start = _subscription_item_period(subscription_object, "current_period_start")
            existing.current_period_end = _subscription_item_period(subscription_object, "current_period_end")
            existing.cancel_at_period_end = bool(_obj_get(subscription_object, "cancel_at_period_end", False))
            self.session.add(existing)
            await self.session.flush()

        user = await self.session.get(User, user_id)
        if user is not None:
            from app.usage.service import UsageService

            await UsageService(self.session).recalculate_current_period(user)
        return existing

    async def _upsert_billing_account(
        self, user_id: uuid.UUID, stripe_customer_id: str
    ) -> UserBillingAccount:
        account = (
            await self.session.execute(
                select(UserBillingAccount).where(UserBillingAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
        if account is None:
            account = UserBillingAccount(user_id=user_id)
        account.stripe_customer_id = stripe_customer_id
        self.session.add(account)
        await self.session.flush()
        return account

    async def _plan_id_for_subscription(
        self, subscription_object: Any, price_id: str | None
    ) -> uuid.UUID | None:
        # The current price is the authoritative answer to "which plan is this
        # subscription actually on" — metadata.plan_id is only set once, at
        # Checkout creation, and goes stale the moment the subscription is moved
        # to a different price (a plan switch keeps paying customers on the old
        # plan's allowance forever if metadata is trusted over the live price).
        if price_id:
            # A webhook can be the first billing traffic a fresh database sees, and
            # the price -> plan lookup below needs the catalog to exist.
            await seed_default_billing_plans(self.session)
            result = await self.session.execute(
                select(BillingPlan).where(
                    (BillingPlan.stripe_price_id == price_id)
                    | (BillingPlan.stripe_annual_price_id == price_id)
                )
            )
            plan = result.scalar_one_or_none()
            if plan is not None:
                return plan.id
        metadata = _metadata(subscription_object)
        plan_id = metadata.get("plan_id")
        return uuid.UUID(str(plan_id)) if plan_id else None

    async def _active_subscription(self, user_id: uuid.UUID) -> UserSubscription | None:
        result = await self.session.execute(
            select(UserSubscription).where(
                UserSubscription.user_id == user_id,
                UserSubscription.status.in_(("active", "trialing")),
            )
        )
        return result.scalar_one_or_none()

    async def _mark_subscription_status(self, stripe_subscription_id: str, status: str) -> None:
        subscription = (
            await self.session.execute(
                select(UserSubscription).where(
                    UserSubscription.stripe_subscription_id == stripe_subscription_id
                )
            )
        ).scalar_one_or_none()
        if subscription is not None:
            subscription.status = status
            self.session.add(subscription)
