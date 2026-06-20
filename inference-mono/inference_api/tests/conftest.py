import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

TEST_DB_PATH = Path("/tmp/tee_inference_api_test.sqlite")
TEST_DB_PATH.unlink(missing_ok=True)

os.environ.update(
    {
        "APP_ENV": "test",
        "DEBUG": "false",
        "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DB_PATH}",
        "REDIS_URL": "redis://localhost:6379/15",
        "RATE_LIMIT_ENABLED": "false",
        "RATE_LIMIT_FAIL_OPEN": "true",
        "SECRET_PEPPER": "test-pepper-secret",
        "ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "CORS_ORIGINS": "",
    }
)

from inference_api.db import async_session_maker, engine  # noqa: E402
from inference_api.main import create_app  # noqa: E402
from inference_api.models import (  # noqa: E402
    APIKey,
    Base,
    ManualTokenAdjustment,
    ServiceClient,
    ServiceClientRole,
    User,
)
from inference_api.security import keyed_hash, utcnow  # noqa: E402


@dataclass(frozen=True)
class SeededAPIKey:
    raw_key: str
    user_id: uuid.UUID
    api_key_id: uuid.UUID


@dataclass(frozen=True)
class SeededRouterToken:
    raw_token: str
    router_client_id: uuid.UUID


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


@pytest_asyncio.fixture
async def seed_api_key():
    async def factory(
        *,
        raw_key: str = "sk_test_valid",
        scopes: list[str] | None = None,
        credits: int = 0,
        expires_at: datetime | None = None,
        revoked_at: datetime | None = None,
        user_active: bool = True,
    ) -> SeededAPIKey:
        user = User(id=uuid.uuid4(), is_active=user_active, created_at=utcnow())
        api_key = APIKey(
            id=uuid.uuid4(),
            user_id=user.id,
            hashed_key=keyed_hash(raw_key),
            scopes=scopes or ["models:read", "inference:write", "usage:read"],
            rate_limit_per_minute=120,
            expires_at=expires_at,
            revoked_at=revoked_at,
        )
        records = [user, api_key]
        if credits:
            records.append(
                ManualTokenAdjustment(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    amount=credits,
                    created_at=utcnow(),
                )
            )
        async with async_session_maker() as session:
            session.add_all(records)
            await session.commit()
        return SeededAPIKey(raw_key=raw_key, user_id=user.id, api_key_id=api_key.id)

    return factory


@pytest_asyncio.fixture
async def seed_router_token():
    async def factory(
        *,
        raw_token: str = "rk_live_router",
        expires_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> SeededRouterToken:
        router_client = ServiceClient(
            id=uuid.uuid4(),
            role=ServiceClientRole.router,
            hashed_token=keyed_hash(raw_token),
            rate_limit_per_minute=600,
            expires_at=expires_at,
            revoked_at=revoked_at,
        )
        async with async_session_maker() as session:
            session.add(router_client)
            await session.commit()
        return SeededRouterToken(raw_token=raw_token, router_client_id=router_client.id)

    return factory
