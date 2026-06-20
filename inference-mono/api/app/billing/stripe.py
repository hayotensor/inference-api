from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.errors import service_unavailable


def _secret_value(secret) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value or None


class StripeClient:
    def _stripe(self):
        secret_key = _secret_value(settings.stripe_secret_key)
        if not secret_key:
            raise service_unavailable("stripe_not_configured", "Stripe secret key is not configured")
        try:
            import stripe
        except ImportError as exc:
            raise service_unavailable("stripe_sdk_missing", "Stripe SDK is not installed") from exc
        stripe.api_key = secret_key
        return stripe

    def create_customer(self, *, email: str, name: str | None, user_id: str) -> str:
        stripe = self._stripe()
        customer = stripe.Customer.create(
            email=email,
            name=name,
            metadata={"user_id": user_id},
        )
        return customer["id"]

    def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: str,
        plan_id: str,
    ) -> dict[str, Any]:
        stripe = self._stripe()
        return stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=str(settings.stripe_success_url),
            cancel_url=str(settings.stripe_cancel_url),
            client_reference_id=user_id,
            metadata={"user_id": user_id, "plan_id": plan_id},
            subscription_data={"metadata": {"user_id": user_id, "plan_id": plan_id}},
        )

    def create_customer_portal_session(self, *, customer_id: str) -> dict[str, Any]:
        stripe = self._stripe()
        return stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=str(settings.stripe_customer_portal_return_url),
        )

    def cancel_subscription(self, *, subscription_id: str, cancel_at_period_end: bool) -> dict[str, Any]:
        stripe = self._stripe()
        if cancel_at_period_end:
            return stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        return stripe.Subscription.delete(subscription_id)

    def update_subscription_price(
        self,
        *,
        subscription_id: str,
        subscription_item_id: str,
        price_id: str,
    ) -> dict[str, Any]:
        stripe = self._stripe()
        return stripe.Subscription.modify(
            subscription_id,
            items=[{"id": subscription_item_id, "price": price_id}],
            proration_behavior="create_prorations",
        )

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        stripe = self._stripe()
        return stripe.Subscription.retrieve(subscription_id)

    def construct_webhook_event(self, *, payload: bytes, signature: str) -> dict[str, Any]:
        endpoint_secret = _secret_value(settings.stripe_webhook_secret)
        if not endpoint_secret:
            raise service_unavailable("stripe_webhook_not_configured", "Stripe webhook secret is not configured")
        stripe = self._stripe()
        try:
            return stripe.Webhook.construct_event(payload, signature, endpoint_secret)
        except Exception as exc:
            from app.core.errors import bad_request

            raise bad_request("invalid_stripe_signature", "Invalid Stripe webhook signature") from exc
