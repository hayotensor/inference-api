# Deployment Guide

The system now has three deployable repos/services:

- Main API: account auth, API-key lifecycle, billing, admin, wallets, migrations.
- TEE inference API: `/v1/*` inference endpoints and `/router/inference/*`.
- UI: Next.js frontend.

In this checkout those services live at:

- Main API: `inference-mono/api`
- TEE inference API: `inference-mono/inference_api`
- UI: `inference-mono/ui`

If you split them into three repos, use sibling directories such as:

- `api`
- `inference_api`
- `ui`

The production compose file supports both layouts through `API_CONTEXT`, `INFERENCE_API_CONTEXT`, `UI_CONTEXT`, `API_ENV_FILE`, and `INFERENCE_API_ENV_FILE`.

## Runtime Shape

- Postgres is shared by the main API and inference API.
- Redis is shared by both APIs.
- Only the main API runs Alembic migrations.
- `SECRET_PEPPER` must be identical in the main API and inference API, because both validate API keys and router service tokens.
- The UI uses `NEXT_PUBLIC_API_BASE_URL` for account/admin/billing calls and `NEXT_PUBLIC_INFERENCE_API_BASE_URL` for inference calls.
- Put the inference API behind the TEE boundary. Keep login, API-key management, billing, and admin traffic on the main API.

## Local Development

From the current monorepo root:

```bash
cd /home/bob/inference-api

if [ ! -f inference-mono/api/.env ]; then
  cp inference-mono/api/.env.example inference-mono/api/.env
fi

if [ ! -f inference-mono/inference_api/.env ]; then
  cp inference-mono/inference_api/.env.example inference-mono/inference_api/.env
fi

# Keep API-key hashing compatible between services.
PEPPER="$(grep '^SECRET_PEPPER=' inference-mono/api/.env | cut -d= -f2-)"
sed -i "s|^SECRET_PEPPER=.*|SECRET_PEPPER=${PEPPER}|" inference-mono/inference_api/.env

docker compose -f inference-mono/api/docker-compose.yml up -d --build

curl -f http://localhost:8000/health
curl -f http://localhost:8001/health
```

Start the UI separately:

```bash
cd /home/bob/inference-api/inference-mono/ui

if [ ! -f .env.local ]; then
  cp .env.example .env.local
fi

corepack enable
pnpm install
pnpm dev
```

Open:

- UI: `http://localhost:3000`
- Main API docs: `http://localhost:8000/docs`
- Inference API docs: `http://localhost:8001/docs`

Stop the local API stack:

```bash
cd /home/bob/inference-api
docker compose -f inference-mono/api/docker-compose.yml down
```

Create the first local admin owner:

```bash
cd /home/bob/inference-api
OWNER_EMAIL=you@example.com
docker compose -f inference-mono/api/docker-compose.yml exec api \
  python -m app.admin.bootstrap_owner --email "$OWNER_EMAIL"
```

For a local three-repo checkout where `api` and `inference_api` are sibling directories, run the compose file from the API repo and override the inference path only if needed:

```bash
cd /path/to/api
INFERENCE_API_CONTEXT=../inference_api \
INFERENCE_API_ENV_FILE=../inference_api/.env \
docker compose -f docker-compose.yml up -d --build
```

## Live: One Ubuntu Server

This path runs:

- Postgres container, not exposed publicly.
- Redis container, not exposed publicly.
- Main API on `127.0.0.1:8000`.
- TEE inference API on `127.0.0.1:8001`.
- UI on `127.0.0.1:3000`.
- Caddy on the host for HTTPS and reverse proxying.

Create three DNS `A` records pointing at the server IP:

- `APP_DOMAIN`, for example `app.example.com`
- `API_DOMAIN`, for example `api.example.com`
- `INFERENCE_API_DOMAIN`, for example `inference.example.com`

### 1. Install Server Packages

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3 ufw

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

### 2. Put Code On The Server

Pick one layout.

Monorepo layout:

```bash
REPO_URL='git@github.com:YOUR_ORG/YOUR_MONOREPO.git'

sudo mkdir -p /opt/inference-system
sudo chown -R "$USER:$USER" /opt/inference-system

if [ -d /opt/inference-system/.git ]; then
  cd /opt/inference-system
  git pull --ff-only
else
  git clone "$REPO_URL" /opt/inference-system
  cd /opt/inference-system
fi
```

Three-repo layout:

```bash
sudo mkdir -p /opt/inference-system
sudo chown -R "$USER:$USER" /opt/inference-system
cd /opt/inference-system

git clone git@github.com:YOUR_ORG/YOUR_API_REPO.git api
git clone git@github.com:YOUR_ORG/YOUR_INFERENCE_API_REPO.git inference_api
git clone git@github.com:YOUR_ORG/YOUR_UI_REPO.git ui

# Keep docker-compose.production.yml in this deployment directory.
# Copy it from whichever repo owns deployment config, or keep it in a small deploy repo.
```

For updates in the three-repo layout:

```bash
cd /opt/inference-system/api && git pull --ff-only
cd /opt/inference-system/inference_api && git pull --ff-only
cd /opt/inference-system/ui && git pull --ff-only
cd /opt/inference-system
```

### 3. Create Production Env Files

Set domains and service paths first.

For the current monorepo layout:

```bash
cd /opt/inference-system
export API_DIR='inference-mono/api'
export INFERENCE_API_DIR='inference-mono/inference_api'
export UI_DIR='inference-mono/ui'
```

For a three-repo sibling layout:

```bash
cd /opt/inference-system
export API_DIR='api'
export INFERENCE_API_DIR='inference_api'
export UI_DIR='ui'
```

Then generate env files:

```bash
export APP_DOMAIN='app.example.com'
export API_DOMAIN='api.example.com'
export INFERENCE_API_DOMAIN='inference.example.com'
export PHONE_USER_EMAIL_DOMAIN='phone-users.example.com'
export NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID=''
export MESH_ROUTER_URL=''

python3 - <<'PY'
from pathlib import Path
import os
import secrets


def token() -> str:
    return secrets.token_urlsafe(48)


def clean_host(value: str) -> str:
    value = value.strip()
    for prefix in ("https://", "http://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.strip("/").split("/")[0]


def clean_dir(name: str, fallback: str) -> Path:
    return Path(os.environ.get(name, fallback)).expanduser()


required = ("APP_DOMAIN", "API_DOMAIN", "INFERENCE_API_DOMAIN", "PHONE_USER_EMAIL_DOMAIN")
missing = [name for name in required if not os.environ.get(name)]
if missing:
    raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

app_domain = clean_host(os.environ["APP_DOMAIN"])
api_domain = clean_host(os.environ["API_DOMAIN"])
inference_api_domain = clean_host(os.environ["INFERENCE_API_DOMAIN"])
phone_domain = clean_host(os.environ["PHONE_USER_EMAIL_DOMAIN"])
walletconnect_project_id = os.environ.get("NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID", "")
mesh_router_url = os.environ.get("MESH_ROUTER_URL", "")

api_dir = clean_dir("API_DIR", "inference-mono/api")
inference_api_dir = clean_dir("INFERENCE_API_DIR", "inference-mono/inference_api")
ui_dir = clean_dir("UI_DIR", "inference-mono/ui")

postgres_password = os.environ.get("POSTGRES_PASSWORD") or secrets.token_urlsafe(32)
secret_pepper = token()

for directory in (api_dir, inference_api_dir):
    directory.mkdir(parents=True, exist_ok=True)

Path(".env.production").write_text(
    "\n".join(
        [
            f"POSTGRES_PASSWORD={postgres_password}",
            f"API_CONTEXT={api_dir.as_posix()}",
            f"INFERENCE_API_CONTEXT={inference_api_dir.as_posix()}",
            f"UI_CONTEXT={ui_dir.as_posix()}",
            f"API_ENV_FILE={(api_dir / '.env').as_posix()}",
            f"INFERENCE_API_ENV_FILE={(inference_api_dir / '.env').as_posix()}",
            f"NEXT_PUBLIC_API_BASE_URL=https://{api_domain}",
            f"NEXT_PUBLIC_INFERENCE_API_BASE_URL=https://{inference_api_domain}",
            f"NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID={walletconnect_project_id}",
            "",
        ]
    )
)

(api_dir / ".env").write_text(
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

(inference_api_dir / ".env").write_text(
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
            f"MESH_ROUTER_URL={mesh_router_url}",
            "MESH_REQUEST_TIMEOUT_SECONDS=120",
            "",
        ]
    )
)

print("Wrote .env.production")
print(f"Wrote {api_dir / '.env'}")
print(f"Wrote {inference_api_dir / '.env'}")
PY
```

For production users, edit the generated env files before launch and configure real email/SMS/Stripe/OAuth settings. The generator uses console email/SMS so the services can boot without third-party accounts.

### 4. Start The Stack

```bash
cd /opt/inference-system

sudo docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
sudo docker compose --env-file .env.production -f docker-compose.production.yml ps

curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8001/health
curl -I http://127.0.0.1:3000
```

The `api` container runs migrations before starting. The `inference_api` container never runs migrations.

### 5. Put HTTPS In Front

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

Test from your machine:

```bash
curl -f "https://${API_DOMAIN}/health"
curl -f "https://${INFERENCE_API_DOMAIN}/health"
```

Open `https://${APP_DOMAIN}` in your browser.

### 6. Use The System

1. Users sign up and log in through the UI, which talks to `API_DOMAIN`.
2. Users create API keys through the dashboard or main API.
3. Inference calls go to `INFERENCE_API_DOMAIN`, not `API_DOMAIN`.
4. Admins create router service clients through the main API.
5. Router clients call `/router/inference/*` on `INFERENCE_API_DOMAIN` with their router token and `X-User-API-Key`.

Example product calls:

```bash
curl "https://${INFERENCE_API_DOMAIN}/v1/models" \
  -H "Authorization: Bearer sk_test_xxx"

curl -X POST "https://${INFERENCE_API_DOMAIN}/v1/inference" \
  -H "X-API-Key: sk_test_xxx" \
  -H "Content-Type: application/json" \
  -d '{"model":"demo-inference-001","prompt":"Hello"}'

curl "https://${INFERENCE_API_DOMAIN}/v1/usage" \
  -H "Authorization: Bearer sk_test_xxx"
```

### 7. Create The First Live Admin Owner

```bash
cd /opt/inference-system
OWNER_EMAIL='you@example.com'

sudo docker compose --env-file .env.production -f docker-compose.production.yml exec api \
  python -m app.admin.bootstrap_owner --email "$OWNER_EMAIL"
```

Then log in and open `https://${APP_DOMAIN}/admin`.

### 8. Optional Production Integrations

Main API settings in `${API_DIR}/.env`:

- Google OAuth callback: `https://${API_DOMAIN}/auth/google/callback`
- Apple OAuth callback: `https://${API_DOMAIN}/auth/apple/callback`
- Stripe webhook endpoint: `https://${API_DOMAIN}/billing/webhook`
- Stripe success URL: `https://${APP_DOMAIN}/billing/success`
- Stripe cancel URL: `https://${APP_DOMAIN}/billing/cancel`
- Stripe customer portal return URL: `https://${APP_DOMAIN}/dashboard/billing`

Inference API settings in `${INFERENCE_API_DIR}/.env`:

- `MESH_ROUTER_URL`, the peer mesh router URL used by `/v1/inference`.
- `ROUTER_RESERVATION_TTL_SECONDS`
- `ROUTER_MAX_INPUT_TOKENS`
- `ROUTER_MAX_OUTPUT_TOKENS`

UI settings in `.env.production`:

- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_INFERENCE_API_BASE_URL`
- `NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID`

The UI reads `NEXT_PUBLIC_*` values at build time. Rebuild the UI after changing `.env.production`.

### 9. Redeploy Updates

Monorepo:

```bash
cd /opt/inference-system
git pull --ff-only
sudo docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f api inference_api ui
```

Three repos:

```bash
cd /opt/inference-system/api && git pull --ff-only
cd /opt/inference-system/inference_api && git pull --ff-only
cd /opt/inference-system/ui && git pull --ff-only
cd /opt/inference-system

sudo docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f api inference_api ui
```

### 10. Useful Live Commands

```bash
cd /opt/inference-system

sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f api
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f inference_api
sudo docker compose --env-file .env.production -f docker-compose.production.yml logs -f ui

sudo docker compose --env-file .env.production -f docker-compose.production.yml exec api alembic upgrade head

sudo docker compose --env-file .env.production -f docker-compose.production.yml restart

sudo docker compose --env-file .env.production -f docker-compose.production.yml down
```

References:

- Docker Engine Ubuntu install: https://docs.docker.com/engine/install/ubuntu/
- Caddy Debian/Ubuntu install: https://caddyserver.com/docs/install
