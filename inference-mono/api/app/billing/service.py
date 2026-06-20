from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import seed_default_billing_plans
from app.billing.stripe import StripeClient
from app.core.errors import bad_request, not_found
from app.core.security import utcnow
from app.db.models.billing_plan import BillingPlan
from app.db.models.stripe_webhook_event import StripeWebhookEvent
from app.db.models.user import User
from app.db.models.user_billing_account import UserBillingAccount
from app.db.models.user_subscription import UserSubscription


def _obj_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _metadata(value: Any) -> dict[str, Any]:
    metadata = _obj_get(value, "metadata", {}) or {}
    return dict(metadata)


def _stripe_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value), tz=UTC)


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
        return list(result.scalars())

    async def create_checkout_session(self, user: User, plan_id: uuid.UUID) -> dict[str, Any]:
        await seed_default_billing_plans(self.session)
        plan = await self.session.get(BillingPlan, plan_id)
        if plan is None or not plan.active or not plan.visible:
            raise not_found("Billing plan not found")
        if not plan.stripe_price_id:
            raise bad_request("stripe_price_missing", "Billing plan is missing a Stripe price ID")

        active_subscription = await self._active_subscription(user.id)
        if active_subscription is not None:
            raise bad_request(
                "subscription_exists",
                "Use the Stripe customer portal to change an existing subscription",
            )

        account = await self.ensure_billing_account(user)
        session = self.stripe_client.create_checkout_session(
            customer_id=account.stripe_customer_id or "",
            price_id=plan.stripe_price_id,
            user_id=str(user.id),
            plan_id=str(plan.id),
        )
        return {"id": session["id"], "url": session["url"]}

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
        if subscription.plan_id is not None:
            plan = await self.session.get(BillingPlan, subscription.plan_id)
            plan_name = plan.name if plan else None
        return {
            "id": subscription.id,
            "stripe_subscription_id": subscription.stripe_subscription_id,
            "stripe_price_id": subscription.stripe_price_id,
            "plan_id": subscription.plan_id,
            "plan_name": plan_name,
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
        subscription = _obj_get(checkout_session, "subscription")
        if subscription:
            if isinstance(subscription, str):
                subscription = self.stripe_client.retrieve_subscription(subscription)
            await self.sync_subscription_object(subscription)

    async def sync_invoice_subscription(self, invoice: Any, event_type: str) -> None:
        subscription_id = _obj_get(invoice, "subscription")
        if not subscription_id:
            return
        try:
            subscription = self.stripe_client.retrieve_subscription(str(subscription_id))
            await self.sync_subscription_object(subscription)
        except Exception:
            if event_type == "invoice.payment_failed":
                await self._mark_subscription_status(str(subscription_id), "past_due")

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
        if existing is None:
            existing = UserSubscription(user_id=user_id)
        existing.stripe_subscription_id = stripe_subscription_id
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
        metadata = _metadata(subscription_object)
        plan_id = metadata.get("plan_id")
        if plan_id:
            return uuid.UUID(str(plan_id))
        if price_id:
            result = await self.session.execute(
                select(BillingPlan).where(BillingPlan.stripe_price_id == price_id)
            )
            plan = result.scalar_one_or_none()
            return plan.id if plan else None
        return None

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
