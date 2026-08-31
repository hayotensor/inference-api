# Talaris frontend + chat integration — runbook

How the dashboard, wallet/Stripe onboarding, and the rebranded Open-WebUI chat
fit together behind Cloudflare. Companion repos: `../talaris-webui` (chat),
`../talaris-edge` (Worker + broker + tunnel).

## Topology

```
app.talaris.xyz ──► Cloudflare Worker (talaris-edge/worker)
    /chat/* ─► Open-WebUI (talaris-webui)  ── OPENAI_API_BASE_URL ─► broker ─► inference_api:8001
    /*      ─► Next.js dashboard (inference-mono/ui) ─► api:8000 / inference_api:8001
postgres: DB `inference` (api + inference)  +  DB `openwebui` (Open-WebUI)
```

- Public exposure is via **Cloudflare Tunnel** — no inbound host ports.
- The **Worker** does path routing + SSO trusted-header injection (and stripping).
- The **broker** maps the chat user → their managed `sk_` key so usage bills right.

## One-time setup

1. **Secrets** — set a shared `SSO_SECRET` in the API env and the Worker
   (`wrangler secret put SSO_SECRET`). Set the API to production email:
   `EMAIL_PROVIDER=resend`, `RESEND_API_KEY=…`, `EMAIL_FROM=no-reply@talaris.xyz`.
2. **DNS / email (Cloudflare)** — point `app.talaris.xyz` at the Worker; add
   Resend's DKIM/SPF/DMARC records for the `EMAIL_FROM` domain. (Cloudflare Email
   Routing is inbound-only and does not send these.)
3. **CORS / hosts** — add `https://app.talaris.xyz` to `CORS_ORIGINS` and its host
   to `ALLOWED_HOSTS` in **both** `api` and `inference_api` env.
4. **Edge service token** — create a service client with `role=edge`
   (`POST /admin/service-clients`), put the returned `rk_…` in the broker as
   `BROKER_EDGE_SERVICE_TOKEN`.
5. **Broker ingress secret** — pick a random `BROKER_INGRESS_SECRET` and set the
   same value as Open-WebUI's `OPENAI_API_KEY`.
6. **Open-WebUI DB** — created automatically on a fresh Postgres volume (see
   `postgres-init/`); otherwise `CREATE DATABASE openwebui OWNER inference;`.

## Bring it up

```
cd inference-api
docker compose -f docker-compose.production.yml -f docker-compose.talaris.yml up -d --build
cloudflared tunnel --config ../talaris-edge/cloudflared/config.yml run   # if not containerized
```

## Smoke test

1. Open `https://app.talaris.xyz` → sign in with wallet or sign up + Stripe.
2. Click **Open Chat** → lands in `/chat` already logged in (no second login).
3. Send a message → reply streams; `GET /usage/current` shows `used_tokens` up on
   your account.
4. Trigger a password reset → email arrives via Resend with a link to the UI.

## What changed in this repo

- **SIWE login**: `app/auth/wallet.py`, `/auth/wallet/{nonce,verify}`, migration
  `20260716_0008`, `users.wallet_verified_at`, API-key gate now accepts wallets.
- **SSO cookie**: `AuthService.issue_sso_token` + `POST /auth/sso/cookie`.
- **Chat-key broker**: `ServiceClientRole.edge`, `APIKeyService.mint_managed_chat_key`,
  `app/internal/routes.py` (`/internal/users/{id|by-email}/chat-key`).
- **Email**: verification/reset emails now link into the UI (`app/email/service.py`).
- **Deploy**: `docker-compose.talaris.yml`, `postgres-init/`.
