# Stripe Billing

The backend owns Stripe. The UI never talks to Stripe's API: it asks FastAPI for a
Checkout or customer-portal session and redirects to the URL it gets back, so only
the API process ever sees the secret key.

```
UI (Next.js)                 API (FastAPI)                    Stripe
  POST /billing/stripe/checkout-session ──▶ Checkout.Session.create ──▶ hosted page
  window.location = session.url ─────────────────────────────────────▶ customer pays
                               ◀── POST /billing/stripe/webhook ────── events
                                    (syncs user_subscriptions,
                                     recalculates the token allowance)
```

Endpoints:

| Endpoint | Purpose |
| -------- | ------- |
| `GET /billing/plans` | The catalog, with amounts, currency, and both price IDs |
| `POST /billing/stripe/checkout-session` | Start a subscription (`interval`: `month`/`year`) |
| `POST /billing/subscription/change` | Move an existing subscription to another plan/interval |
| `POST /billing/subscription/cancel` | Cancel, at period end by default |
| `POST /billing/stripe/customer-portal` | Invoices and payment methods |
| `POST /billing/stripe/webhook` | Stripe → us |

Checkout refuses when a subscription already exists; that customer changes plans
through `/billing/subscription/change`, which updates the subscription item in place
and lets Stripe prorate. The customer portal can also switch plans, but only on
accounts where portal plan-switching is configured — the app does not rely on it.

## The plan catalog

`inference-mono/api/app/billing/plans.py` is the source of truth. Every plan is
seeded into `billing_plans` on the first `/billing/plans` call and carries the
amount it displays, so the pricing page never has to guess.

| Plan     | Allowance / month | Monthly | Yearly (2 months free) | Effective rate |
| -------- | ----------------- | ------- | ---------------------- | -------------- |
| Free     | 100,000           | $0      | —                      | —              |
| Starter  | 1,000,000         | $9      | $90                    | $9.00 / M      |
| Pro      | 10,000,000        | $49     | $490                   | $4.90 / M      |
| Business | 100,000,000       | $299    | $2,990                 | $2.99 / M      |

The Free allowance comes from `FREE_MONTHLY_TOKEN_ALLOWANCE`, not from the table.
Allowances are per month on both intervals — an annual plan is the same monthly
allowance, billed once a year.

## Configuring an account

1. Put the keys in `inference-mono/api/.env`:

   ```
   STRIPE_SECRET_KEY=sk_test_...
   STRIPE_PUBLISHABLE_KEY=pk_test_...
   ```

2. Create the products, prices, and portal configuration, and write the IDs back:

   ```bash
   cd inference-mono/api
   python scripts/stripe_sync_plans.py --dry-run     # review
   python scripts/stripe_sync_plans.py --write-env
   ```

   The script is idempotent — products are matched by the `talaris_plan_slug`
   metadata key and prices by (interval, currency, amount). Re-running it after a
   plan is already set up creates nothing. It refuses to touch a live account
   without an interactive confirmation.

3. Wire the webhook. Locally:

   ```bash
   stripe listen --forward-to localhost:8000/billing/stripe/webhook
   ```

   Copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`. For a deployed API,
   let the script create the endpoint instead — the signing secret is only
   readable at creation time:

   ```bash
   python scripts/stripe_sync_plans.py --write-env \
     --webhook-url https://api.example.com/billing/stripe/webhook
   ```

4. Give the UI the publishable key in `inference-mono/ui/.env.local`:

   ```
   NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_test_...
   ```

   The UI only uses it to label test-mode billing. `NEXT_PUBLIC_*` values are baked
   in at build time, so rebuild the UI after changing it.

5. Redirect URLs must point at the **UI**, not the API — Stripe sends a browser
   there, and the API has no such pages:

   ```
   STRIPE_SUCCESS_URL=http://localhost:3000/billing/success
   STRIPE_CANCEL_URL=http://localhost:3000/billing/cancel
   STRIPE_CUSTOMER_PORTAL_RETURN_URL=http://localhost:3000/dashboard/billing
   ```

`STRIPE_PORTAL_CONFIGURATION_ID` matters on a fresh account: the customer portal
cannot open a session until a configuration exists, and a new account has none.
The sync script creates one (cancel-at-period-end, invoice history, payment-method
updates, and plan switching where the account stores it) and prints its `bpc_...` ID.
An unactivated sandbox accepts the switchable-product list but does not store it —
the script says so when that happens, and plan switching in the app goes through
`/billing/subscription/change` regardless.

## Testing a payment

With `stripe listen` running, pick a paid plan in the dashboard and pay with
`4242 4242 4242 4242`, any future expiry, any CVC. Then check:

- `/billing/subscription` reports `status: active` with the interval and amount.
- `/usage/allowance` shows `stripe_monthly_allowance` equal to the plan allowance
  (the webhook triggers `UsageService.recalculate_current_period`).
- `4000 0000 0000 0341` (payment fails after attaching) exercises the
  `invoice.payment_failed` → `past_due` path.

## Changing a price

Stripe prices are immutable. Edit the catalog amount in `plans.py`, then re-run the
sync script: it creates a new price, repoints the product's `default_price`, and
prints the new ID. Existing subscribers stay on the old price until they are
migrated — pass `--archive-stale` to deactivate the superseded prices once nobody
is on them. `plans.py` keeps the displayed amount tied to the price ID it points
at, so the UI cannot advertise an amount Stripe would not charge.

Per-plan overrides are also editable at `/admin/billing/plans` (needs the
`billing.plans.write` permission). The seeder does not clobber an amount set there
unless the price ID in the env changes.

## Gotchas that cost real money

- **The webhook is the only thing that grants a paid allowance.** Without
  `STRIPE_WEBHOOK_SECRET` wired to a reachable endpoint, a customer can pay and
  still be capped at the Free allowance. `/billing/subscription` will also keep
  saying `free`.
- **Redirect URLs must point at the UI.** They used to default to port 8000 (the
  API), where those paths do not exist.
- **A subscription can arrive without period bounds** — newer Stripe API versions
  put `current_period_start/end` on the subscription *item*. The allowance keys off
  the subscription itself, so a missing window falls back to the configured reset
  mode without downgrading the customer to the free tier.

## Not wired up

- **Metered overage.** Token usage above the allowance is refused, not billed.
  Usage is metered in `usage_periods` / `inference_usage_events`, but nothing
  reports usage records to Stripe.
- **Tax, coupons, trials, proration previews.** Stripe defaults apply; the portal
  handles proration on plan switches.
- **Crypto allowances** (`ethereum_erc20`, `subnet_stake`, manual adjustments) are
  additive to the Stripe allowance and have nothing to do with Stripe.
