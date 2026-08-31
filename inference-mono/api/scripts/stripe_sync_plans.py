#!/usr/bin/env python
"""Create (or verify) the Stripe products and prices for the plan catalog.

The catalog in ``app/billing/plans.py`` is the source of truth: this script makes
Stripe match it and prints the ``STRIPE_*_PRICE_ID`` lines the API needs. It is
idempotent — products are matched by the ``talaris_plan_slug`` metadata key and
prices by (interval, currency, amount), so re-running it creates nothing new.

Stripe prices are immutable. When the catalog amount changes, this script creates
a NEW price and points the product's ``default_price`` at it; the old price stays
active so existing subscribers keep their agreed rate until they are migrated
(pass ``--archive-stale`` to deactivate them instead).

Usage (from inference-mono/api, with .env holding STRIPE_SECRET_KEY):

    python scripts/stripe_sync_plans.py --dry-run       # show the plan
    python scripts/stripe_sync_plans.py --write-env     # sync + update .env
    python scripts/stripe_sync_plans.py --webhook-url https://api.example.com/billing/stripe/webhook
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

PRODUCT_SLUG_KEY = "talaris_plan_slug"

# The events app/billing/service.py:process_webhook actually handles.
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.updated",
]

PORTAL_MANAGED_KEY = "talaris_managed"

ENV_KEYS = {
    ("starter", "month"): "STRIPE_STARTER_PRICE_ID",
    ("pro", "month"): "STRIPE_PRO_PRICE_ID",
    ("business", "month"): "STRIPE_BUSINESS_PRICE_ID",
    ("starter", "year"): "STRIPE_STARTER_ANNUAL_PRICE_ID",
    ("pro", "year"): "STRIPE_PRO_ANNUAL_PRICE_ID",
    ("business", "year"): "STRIPE_BUSINESS_ANNUAL_PRICE_ID",
}


def field(obj, key, default=None):
    """Stripe SDK objects are not plain dicts and have no .get(); read by key."""
    try:
        value = obj[key]
    except (KeyError, TypeError):
        return default
    return default if value is None else value


def money(cents: int, currency: str) -> str:
    return f"{cents / 100:,.2f} {currency.upper()}"


def find_product(stripe, slug: str):
    for product in stripe.Product.list(limit=100, active=True).auto_paging_iter():
        if field(field(product, "metadata", {}), PRODUCT_SLUG_KEY) == slug:
            return product
    return None


def find_price(stripe, product_id: str, *, unit_amount: int, currency: str, interval: str):
    for price in stripe.Price.list(product=product_id, active=True, limit=100).auto_paging_iter():
        recurring = field(price, "recurring", {})
        if (
            field(price, "unit_amount") == unit_amount
            and field(price, "currency") == currency
            and field(recurring, "interval") == interval
            and field(recurring, "interval_count", 1) == 1
        ):
            return price
    return None


def stale_prices(stripe, product_id: str, keep_ids: set[str]):
    return [
        price
        for price in stripe.Price.list(product=product_id, active=True, limit=100).auto_paging_iter()
        if price["id"] not in keep_ids
    ]


def sync_plan(stripe, seed, *, dry_run: bool, archive_stale: bool) -> tuple[str | None, dict[str, str]]:
    intervals = [("month", seed.price_cents), ("year", seed.annual_price_cents)]
    intervals = [(interval, cents) for interval, cents in intervals if cents]
    if not intervals:
        print(f"  {seed.slug:<9} free plan — no Stripe product needed")
        return None, {}

    product = find_product(stripe, seed.slug)
    if product is None:
        if dry_run:
            print(f"  {seed.slug:<9} WOULD CREATE product '{seed.name}'")
            product = {"id": "prod_(dry-run)"}
        else:
            product = stripe.Product.create(
                name=f"Talaris {seed.name}",
                description=seed.description,
                metadata={
                    PRODUCT_SLUG_KEY: seed.slug,
                    "monthly_token_allowance": str(seed.monthly_token_allowance),
                },
            )
            print(f"  {seed.slug:<9} created product {product['id']}")
    else:
        print(f"  {seed.slug:<9} product {product['id']} (existing)")
        if not dry_run:
            stripe.Product.modify(
                product["id"],
                description=seed.description,
                metadata={
                    PRODUCT_SLUG_KEY: seed.slug,
                    "monthly_token_allowance": str(seed.monthly_token_allowance),
                },
            )

    resolved: dict[str, str] = {}
    for interval, cents in intervals:
        existing = None if dry_run and product["id"].endswith("(dry-run)") else find_price(
            stripe, product["id"], unit_amount=cents, currency=seed.currency, interval=interval
        )
        if existing is not None:
            resolved[interval] = existing["id"]
            print(f"    {interval:<5} {money(cents, seed.currency)}  {existing['id']} (existing)")
            continue
        if dry_run:
            print(f"    {interval:<5} {money(cents, seed.currency)}  WOULD CREATE")
            continue
        price = stripe.Price.create(
            product=product["id"],
            unit_amount=cents,
            currency=seed.currency,
            recurring={"interval": interval},
            nickname=f"{seed.name} {interval}ly",
            metadata={PRODUCT_SLUG_KEY: seed.slug, "interval": interval},
        )
        resolved[interval] = price["id"]
        print(f"    {interval:<5} {money(cents, seed.currency)}  {price['id']} (created)")

    if not dry_run and resolved.get("month"):
        # Point the product at the monthly price so the Stripe dashboard and any
        # hosted surface show the headline amount.
        stripe.Product.modify(product["id"], default_price=resolved["month"])

    if not dry_run and archive_stale and resolved:
        for price in stale_prices(stripe, product["id"], set(resolved.values())):
            stripe.Price.modify(price["id"], active=False)
            print(f"    archived stale price {price['id']}")

    return product["id"], resolved


def ensure_portal_configuration(stripe, product_prices: dict[str, list[str]], *, dry_run: bool) -> str | None:
    """The customer portal refuses to open without a configuration, and a brand new
    Stripe account has none. Keep one owned by us, listing every plan product so a
    customer can switch tiers or intervals in the portal."""
    products = [
        {"product": product_id, "prices": prices}
        for product_id, prices in product_prices.items()
        if prices
    ]
    if not products:
        return None
    features = {
        "customer_update": {"enabled": True, "allowed_updates": ["email", "address", "tax_id", "name"]},
        "invoice_history": {"enabled": True},
        "payment_method_update": {"enabled": True},
        "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
        "subscription_update": {
            "enabled": True,
            "default_allowed_updates": ["price"],
            "proration_behavior": "create_prorations",
            "products": products,
        },
    }

    existing = None
    for configuration in stripe.billing_portal.Configuration.list(limit=100).auto_paging_iter():
        metadata = field(configuration, "metadata", {})
        if field(metadata, PORTAL_MANAGED_KEY) == "true" or field(configuration, "is_default") is True:
            existing = configuration
            break

    if dry_run:
        print(f"\nCustomer portal: WOULD {'UPDATE ' + existing['id'] if existing else 'CREATE a configuration'}")
        return existing["id"] if existing else None

    if existing is not None:
        configuration = stripe.billing_portal.Configuration.modify(
            existing["id"], features=features, metadata={PORTAL_MANAGED_KEY: "true"}
        )
        print(f"\nCustomer portal configuration {configuration['id']} (updated)")
    else:
        configuration = stripe.billing_portal.Configuration.create(
            features=features,
            business_profile={"headline": "Talaris Inference"},
            metadata={PORTAL_MANAGED_KEY: "true"},
        )
        print(f"\nCustomer portal configuration {configuration['id']} (created)")

    # Some accounts accept the request but do not store the switchable-product list
    # (an unactivated sandbox will not echo it back). Plan switching in the app does
    # not depend on the portal — POST /billing/subscription/change updates the
    # subscription directly — but say so rather than implying the portal can do it.
    stored = field(field(configuration, "features", {}), "subscription_update", {})
    if not field(stored, "products"):
        print("  note: Stripe did not store the plan list for portal switching; the")
        print("        portal will offer cancel/invoices/payment methods only.")
    return configuration["id"]


def update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(values)
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Z0-9_]+)=", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines[index] = f"{key}={remaining.pop(key)}"
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Added by scripts/stripe_sync_plans.py")
        lines.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(lines) + "\n")
    print(f"\nUpdated {path} ({len(values)} key(s))")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print what would change, call no writes")
    parser.add_argument("--write-env", action="store_true", help="write the price IDs into --env-file")
    parser.add_argument("--env-file", default=str(API_DIR / ".env"), help="env file to update")
    parser.add_argument(
        "--archive-stale",
        action="store_true",
        help="deactivate any other active price on the plan's product (affects no existing subscription)",
    )
    parser.add_argument(
        "--skip-portal", action="store_true", help="do not create/update the customer portal configuration"
    )
    parser.add_argument("--webhook-url", help="also create a webhook endpoint at this URL and print its secret")
    args = parser.parse_args()

    from app.billing.plans import default_plan_seeds
    from app.core.config import settings

    secret_key = settings.stripe_secret_key.get_secret_value() if settings.stripe_secret_key else None
    if not secret_key:
        print("STRIPE_SECRET_KEY is not set (checked env and .env).", file=sys.stderr)
        return 2

    import stripe

    stripe.api_key = secret_key
    account = stripe.Account.retrieve()
    mode = "TEST" if secret_key.startswith("sk_test_") else "LIVE"
    account_name = field(field(account, "business_profile", {}), "name", "-")
    print(f"Stripe account {account['id']} ({account_name}) [{mode}]")
    if mode == "LIVE" and not args.dry_run:
        confirm = input("This is a LIVE key. Type 'yes' to create live products/prices: ")
        if confirm.strip().lower() != "yes":
            return 1

    print("\nPlans:")
    env_values: dict[str, str] = {}
    product_prices: dict[str, list[str]] = {}
    for seed in default_plan_seeds():
        product_id, resolved = sync_plan(stripe, seed, dry_run=args.dry_run, archive_stale=args.archive_stale)
        if product_id:
            product_prices[product_id] = list(resolved.values())
        for interval, price_id in resolved.items():
            key = ENV_KEYS.get((seed.slug, interval))
            if key:
                env_values[key] = price_id

    if not args.skip_portal:
        configuration_id = ensure_portal_configuration(stripe, product_prices, dry_run=args.dry_run)
        if configuration_id:
            env_values["STRIPE_PORTAL_CONFIGURATION_ID"] = configuration_id

    if args.webhook_url and not args.dry_run:
        endpoint = stripe.WebhookEndpoint.create(
            url=args.webhook_url,
            enabled_events=WEBHOOK_EVENTS,
            description="Talaris inference API",
        )
        print(f"\nWebhook endpoint {endpoint['id']} -> {args.webhook_url}")
        secret = field(endpoint, "secret")
        if secret:
            env_values["STRIPE_WEBHOOK_SECRET"] = secret
            print("  signing secret captured (shown only at creation time)")

    if env_values:
        print("\nEnv lines:")
        for key, value in env_values.items():
            print(f"{key}={value}")
        if args.write_env:
            update_env_file(Path(args.env_file), env_values)
        else:
            print("\n(re-run with --write-env to write these into .env)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
