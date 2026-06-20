import os
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TEST_DB_PATH = Path("/tmp/inference_api_test.sqlite")
TEST_DB_PATH.unlink(missing_ok=True)

os.environ.update(
    {
        "APP_ENV": "test",
        "DEBUG": "false",
        "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DB_PATH}",
        "REDIS_URL": "redis://localhost:6379/15",
        "RATE_LIMIT_STORAGE_URL": "memory://",
        "JWT_SECRET": "test-jwt-secret-with-enough-entropy",
        "VERIFICATION_TOKEN_SECRET": "test-verification-secret",
        "RESET_PASSWORD_TOKEN_SECRET": "test-reset-secret",
        "OAUTH_STATE_SECRET": "test-oauth-secret",
        "SESSION_SECRET": "test-session-secret",
        "SECRET_PEPPER": "test-pepper-secret",
        "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "CORS_ORIGINS": "",
        "EMAIL_PROVIDER": "console",
        "SMS_PROVIDER": "console",
        "RATE_LIMIT_FAIL_OPEN": "true",
        "RATE_LIMIT_ENABLED": "false",
    }
)

from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def reset_database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client():
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client
