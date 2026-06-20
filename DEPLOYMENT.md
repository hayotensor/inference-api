# Deployment Guide

This repo is a monorepo:

- API: `inference-mono/api`
- TEE inference API: `inference-mono/inference_api`
- UI: `inference-mono/ui`

Use the local section on your workstation. Use the live section on an Ubuntu VPS with DNS pointed at it.

## Local: API, Inference API, and UI

From the repo root:

```bash
cd /home/bob/inference-api

# API env
if [ ! -f inference-mono/api/.env ]; then
  cp inference-mono/api/.env.example inference-mono/api/.env
fi

# Inference API env. SECRET_PEPPER must match inference-mono/api/.env.
if [ ! -f inference-mono/inference_api/.env ]; then
  cp inference-mono/inference_api/.env.example inference-mono/inference_api/.env
fi

# Start Postgres, Redis, run migrations, the main API, and the inference API.
docker compose -f inference-mono/api/docker-compose.yml up -d --build

# Confirm both APIs are up.
curl -f http://localhost:8000/health
curl -f http://localhost:8001/health

# UI env
if [ ! -f inference-mono/ui/.env.local ]; then
  cp inference-mono/ui/.env.example inference-mono/ui/.env.local
fi

# Start the Next.js UI. This stays in the foreground.
cd inference-mono/ui
corepack enable
pnpm install
pnpm dev
```

Open:

- UI: `http://localhost:3000`
- API health: `http://localhost:8000/health`
- API docs: `http://localhost:8000/docs`
- Inference API health: `http://localhost:8001/health`
- Inference API docs: `http://localhost:8001/docs`

To stop the local API stack:

```bash
cd /home/bob/inference-api
docker compose -f inference-mono/api/docker-compose.yml down
```

To create the first local admin owner:

1. Sign up in the UI at `http://localhost:3000/signup`.
2. Run this from the repo root, replacing the email:

```bash
cd /home/bob/inference-api
OWNER_EMAIL=you@example.com
docker compose -f inference-mono/api/docker-compose.yml exec api \
  python -m app.admin.bootstrap_owner --email "$OWNER_EMAIL"
```

## Live: One Ubuntu Server

This live path runs:

- Postgres container, not exposed publicly
- Redis container, not exposed publicly
- API container on `127.0.0.1:8000`
- Inference API container on `127.0.0.1:8001`
- UI container on `127.0.0.1:3000`
- Caddy on the host for HTTPS and reverse proxying

Before starting, create two DNS `A` records pointing at the server IP:

- `APP_DOMAIN`, for example `app.example.com`
- `API_DOMAIN`, for example `api.example.com`
- `INFERENCE_API_DOMAIN`, for example `inference.example.com`

### 1. Install server packages

Run this on the Ubuntu server. These commands follow the current Docker Engine apt repository flow and Caddy's official Debian/Ubuntu package flow.

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3 ufw

# Docker apt repository.
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

# Caddy apt repository.
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin caddy
sudo systemctl enable --now docker caddy
```

### 2. Put the code on the server

If you deploy from git:

```bash
REPO_URL='git@github.com:YOUR_ORG/YOUR_REPO.git'

sudo mkdir -p /opt/inference-api
sudo chown -R "$USER:$USER" /opt/inference-api

if [ -d /opt/inference-api/.git ]; then
  cd /opt/inference-api
  git pull --ff-only
else
  git clone "$REPO_URL" /opt/inference-api
  cd /opt/inference-api
fi
```

If you copied the files another way, make sure the repo root is `/opt/inference-api`, then:

```bash
cd /opt/inference-api
```

### 3. Create production env files

Set your domains first, then run the generator. `PHONE_USER_EMAIL_DOMAIN` must be a domain you own and should not be the public email domain used by real users.

```bash
cd /opt/inference-api

export APP_DOMAIN='app.example.com'
export API_DOMAIN='api.example.com'
export INFERENCE_API_DOMAIN='inference.example.com'
export PHONE_USER_EMAIL_DOMAIN='phone-users.example.com'
export NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=''

python3 - <<'PY'
from pathlib import Path
import os
import secrets


def clean_host(value: str) -> str:
    value = value.strip()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip("/").split("/")[0]


required = ("APP_DOMAIN", "API_DOMAIN", "INFERENCE_API_DOMAIN", "PHONE_USER_EMAIL_DOMAIN")
missing = [name for name in required if not os.environ.get(name)]
if missing:
    raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

app_domain = clean_host(os.environ["APP_DOMAIN"])
api_domain = clean_host(os.environ["API_DOMAIN"])
inference_api_domain = clean_host(os.environ["INFERENCE_API_DOMAIN"])
phone_domain = clean_host(os.environ["PHONE_USER_EMAIL_DOMAIN"])
walletconnect_project_id = os.environ.get("NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID", "")
postgres_password = os.environ.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(32)
secret_pepper = token()

def token() -> str:
    return secrets.token_urlsafe(48)

Path(".env.production").write_text(
    "\n".join(
        [
            f"POSTGRES_PASSWORD={postgres_password}",
            f"NEXT_PUBLIC_API_BASE_URL=https://{api_domain}",
            f"NEXT_PUBLIC_INFERENCE_API_BASE_URL=https://{inference_api_domain}",
            f"NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID={walletconnect_project_id}",
            "",
        ]
    )
)

Path("inference-mono/api/.env").write_text(
    "\n".join(
        [
            "APP_NAME=Inference API",
            "APP_ENV=production",
            "DEBUG=false",
            f"API_BASE_URL=https://{api_domain}",
            f"DATABASE_URL=postgresql+asyncpg://inference:{postgres_password}@postgres:5432/inference",
            "REDIS_URL=redis://redis:6379/0",
            "RATE_LIMIT_STORAGE_URL=redis://redis:6379/1",
            "RATE_LIMIT_FAIL_OPEN=false",
            "RATE_LIMIT_ENABLED=true",
            "TOKEN_REVOCATION_FAIL_OPEN=false",
            f"JWT_SECRET={token()}",
            "JWT_AUDIENCE=fastapi-users:auth",
            "JWT_LIFETIME_SECONDS=900",
            "REFRESH_TOKEN_LIFETIME_DAYS=30",
            f"VERIFICATION_TOKEN_SECRET={token()}",
            f"RESET_PASSWORD_TOKEN_SECRET={token()}",
            f"OAUTH_STATE_SECRET={token()}",
            f"SESSION_SECRET={token()}",
            f"SECRET_PEPPER={secret_pepper}",
            f"CORS_ORIGINS=https://{app_domain}",
            f"ALLOWED_HOSTS={api_domain},localhost,127.0.0.1",
            "GOOGLE_CLIENT_ID=",
            "GOOGLE_CLIENT_SECRET=",
            "APPLE_CLIENT_ID=",
            "APPLE_CLIENT_SECRET=",
            "EMAIL_PROVIDER=console",
            f"EMAIL_FROM=noreply@{app_domain}",
            "SMTP_HOST=",
            "SMTP_PORT=587",
            "SMTP_USERNAME=",
            "SMTP_PASSWORD=",
            "SMTP_STARTTLS=true",
            "SENDGRID_API_KEY=",
            "RESEND_API_KEY=",
            "AWS_REGION=us-east-1",
            "SMS_PROVIDER=console",
            "DEFAULT_PHONE_REGION=US",
            f"PHONE_USER_EMAIL_DOMAIN={phone_domain}",
            "TWILIO_ACCOUNT_SID=",
            "TWILIO_AUTH_TOKEN=",
            "TWILIO_FROM_NUMBER=",
            "OTP_TTL_SECONDS=300",
            "OTP_MAX_ATTEMPTS=5",
            "API_KEY_DEFAULT_RATE_LIMIT_PER_MINUTE=120",
            "STRIPE_SECRET_KEY=",
            "STRIPE_WEBHOOK_SECRET=",
            f"STRIPE_SUCCESS_URL=https://{app_domain}/billing/success",
            f"STRIPE_CANCEL_URL=https://{app_domain}/billing/cancel",
            f"STRIPE_CUSTOMER_PORTAL_RETURN_URL=https://{app_domain}/dashboard/billing",
            "STRIPE_STARTER_PRICE_ID=",
            "STRIPE_PRO_PRICE_ID=",
            "STRIPE_BUSINESS_PRICE_ID=",
            "ETHEREUM_RPC_URL=",
            "ETHEREUM_ERC20_CONTRACT_ADDRESS=",
            "ETHEREUM_ERC20_DECIMALS=18",
            "SUBSTRATE_EVM_RPC_URL=",
            "SUBSTRATE_NATIVE_DECIMALS=18",
            "TOKEN_RESET_MODE=account_creation",
            "TOKEN_RESET_DAY=1",
            "FREE_MONTHLY_TOKEN_ALLOWANCE=0",
            "WALLET_NONCE_TTL_SECONDS=600",
            "",
        ]
    )
)

Path("inference-mono/inference_api/.env").write_text(
    "\n".join(
        [
            "APP_NAME=TEE Inference API",
            "APP_ENV=production",
            "DEBUG=false",
            f"DATABASE_URL=postgresql+asyncpg://inference:{postgres_password}@postgres:5432/inference",
            "REDIS_URL=redis://redis:6379/0",
            "RATE_LIMIT_ENABLED=true",
            "RATE_LIMIT_FAIL_OPEN=false",
            f"SECRET_PEPPER={secret_pepper}",
            f"CORS_ORIGINS=https://{app_domain}",
            f"ALLOWED_HOSTS={inference_api_domain},localhost,127.0.0.1",
            "REQUEST_ID_HEADER=X-Request-ID",
            "ROUTER_RESERVATION_TTL_SECONDS=900",
            "ROUTER_MAX_INPUT_TOKENS=1000000",
            "ROUTER_MAX_OUTPUT_TOKENS=1000000",
            "TOKEN_RESET_MODE=account_creation",
            "TOKEN_RESET_DAY=1",
            "FREE_MONTHLY_TOKEN_ALLOWANCE=0",
            "MESH_ROUTER_URL=",
            "MESH_REQUEST_TIMEOUT_SECONDS=120",
            "",
        ]
    )
)
print("Wrote .env.production, inference-mono/api/.env, and inference-mono/inference_api/.env")
PY
```

For real users, edit `inference-mono/api/.env` before launch and configure real email/SMS providers. The generated file uses `EMAIL_PROVIDER=console` and `SMS_PROVIDER=console` so the app can boot without third-party accounts.

### 4. Start APIs, UI, Postgres, and Redis

```bash
cd /opt/inference-api

sudo docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
sudo docker compose --env-file .env.production -f docker-compose.production.yml ps

curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8001/health
curl -I http://127.0.0.1:3000
```

### 5. Put HTTPS in front

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
${APP_DOMAIN} {
  reverse_proxy 127.0.0.1:3000
}

${API_DOMAIN} {
  reverse_proxy 127.0.0.1:8000
}

${INFERENCE_API_DOMAIN} {
  reverse_proxy 127.0.0.1:8001
}
EOF

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Now test from your machine:

```bash
curl -f "https://${API_DOMAIN}/health"
curl -f "https://${INFERENCE_API_DOMAIN}/health"
```

Open `https://APP_DOMAIN` in your browser.

### 6. Create the first live admin owner

1. Sign up at `https://APP_DOMAIN/signup`.
2. Run this on the server:

```bash
cd /opt/inference-api
OWNER_EMAIL='you@example.com'

sudo docker compose --env-file .env.production -f docker-compose.production.yml exec api \
  python -m app.admin.bootstrap_owner --email "$OWNER_EMAIL"
```

Then log in and open `https://APP_DOMAIN/admin`.

### 7. Optional production integrations

Set these in `inference-mono/api/.env`, then redeploy with step 8:

- Google OAuth callback: `https://API_DOMAIN/auth/google/callback`
- Apple OAuth callback: `https://API_DOMAIN/auth/apple/callback`
- Stripe webhook endpoint: `https://API_DOMAIN/billing/webhook`
- Stripe success URL: `https://APP_DOMAIN/billing/success`
- Stripe cancel URL: `https://APP_DOMAIN/billing/cancel`
- Stripe customer portal return URL: `https://APP_DOMAIN/dashboard/billing`

For wallet connect features, set `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID` in `.env.production` and rebuild the UI.

### 8. Redeploy updates

```bash
cd /opt/inference-api
git pull --ff-only
sudo docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f api ui
```

The UI reads `NEXT_PUBLIC_*` values at build time. Rebuild the UI after changing `.env.production`.

### 9. Useful live commands

```bash
cd /opt/inference-api

# Logs
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f api
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f ui

# Run migrations manually
sudo docker compose --env-file .env.production -f docker-compose.production.yml exec api alembic upgrade head

# Restart everything
sudo docker compose --env-file .env.production -f docker-compose.production.yml restart

# Stop everything, preserving database volumes
sudo docker compose --env-file .env.production -f docker-compose.production.yml down
```

References:

- Docker Engine Ubuntu install: https://docs.docker.com/engine/install/ubuntu/
- Caddy Debian/Ubuntu install: https://caddyserver.com/docs/install
