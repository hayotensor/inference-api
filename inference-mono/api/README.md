# Main API

API-only FastAPI backend for account, auth, billing, API-key management, admin, wallet, and usage dashboard workflows. Inference endpoints run in the separate `../inference_api` service so they can be deployed behind a TEE.

## Stack

- FastAPI, Pydantic v2
- FastAPI Users with SQLAlchemy 2.x async
- PostgreSQL, Alembic
- Redis, SlowAPI
- Authlib for Google and Apple OAuth redirects
- Argon2 password hashing via FastAPI Users defaults
- pytest, pytest-asyncio, httpx

## Local Setup

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://localhost:8000`.

The standalone inference API is in `../inference_api` and defaults to `http://localhost:8001`.
When running both locally, keep `SECRET_PEPPER` identical in `.env` and `../inference_api/.env`.

Run migrations outside Docker:

```bash
alembic upgrade head
```

Run tests:

```bash
pip install -e ".[dev]"
pytest
```

Run the optional Postgres-backed migration smoke and model-drift check:

```bash
POSTGRES_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/test_db \
  pytest tests/test_migrations.py
```

## Environment

Set strong unique values for `JWT_SECRET`, `VERIFICATION_TOKEN_SECRET`, `RESET_PASSWORD_TOKEN_SECRET`, `SESSION_SECRET`, `OAUTH_STATE_SECRET`, and `SECRET_PEPPER`. Production refuses placeholder or short secrets and requires explicit `ALLOWED_HOSTS`, `CORS_ORIGINS`, `RATE_LIMIT_FAIL_OPEN=false`, `TOKEN_REVOCATION_FAIL_OPEN=false`, and an owned `PHONE_USER_EMAIL_DOMAIN`.

The main API is the only service that runs Alembic migrations. The inference API reads and writes the same database schema but does not own migrations.

Access-token logout and password-reset invalidation use Redis. In production, keep Redis highly available and set `TOKEN_REVOCATION_FAIL_OPEN=false` so token checks fail closed if the denylist cannot be read.

Phone-created accounts use synthetic email addresses under `PHONE_USER_EMAIL_DOMAIN`; use an owned internal domain and do not route real-user email verification flows to those synthetic addresses.

Use `EMAIL_PROVIDER=console` and `SMS_PROVIDER=console` locally. Production options are:

- Email: `smtp`, `sendgrid`, `resend`, `ses`
- SMS: `twilio`, `aws_sns`

## OAuth Setup

Google:

1. Create an OAuth web client in Google Cloud.
2. Add callback URL: `http://localhost:8000/auth/google/callback`.
3. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

Apple:

1. Configure Sign in with Apple for your Services ID.
2. Add callback URL: `http://localhost:8000/auth/apple/callback`.
3. Set `APPLE_CLIENT_ID` and `APPLE_CLIENT_SECRET`.

OAuth callbacks return JSON token responses for API clients.

## SMS Setup

Local development logs OTP messages as structured logs.

Twilio requires `SMS_PROVIDER=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER`.

AWS SNS requires `SMS_PROVIDER=aws_sns`, AWS credentials in the runtime environment, and `AWS_REGION`.

## Email Tokens

Verification and password-reset emails are API-only. The email body contains a token and instructs clients to POST it to `/auth/verify-email` or `/auth/reset-password`; the backend does not create browser redirect flows.

## Example Requests

Register and log in:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"correct-horse-123","full_name":"Dev User"}'

curl -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"correct-horse-123"}'
```

Refresh and logout:

```bash
curl -X POST http://localhost:8000/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<refresh_token>"}'

curl -X POST http://localhost:8000/auth/logout \
  -H 'Authorization: Bearer <jwt>' \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<refresh_token>"}'
```

Create an API key after verifying email or phone:

```bash
curl -X POST http://localhost:8000/api-keys \
  -H 'Authorization: Bearer <jwt>' \
  -H 'Content-Type: application/json' \
  -d '{"name":"local test key","environment":"test","scopes":["models:read","inference:write","usage:read"]}'
```

Call inference APIs through the standalone inference API:

```bash
curl http://localhost:8001/v1/models \
  -H 'Authorization: Bearer sk_test_xxx'

curl -X POST http://localhost:8001/v1/inference \
  -H 'X-API-Key: sk_test_xxx' \
  -H 'Content-Type: application/json' \
  -d '{"model":"demo-inference-001","prompt":"Hello"}'

curl http://localhost:8001/v1/usage \
  -H 'Authorization: Bearer sk_test_xxx'
```
