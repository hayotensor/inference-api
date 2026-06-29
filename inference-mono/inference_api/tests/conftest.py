import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from nacl.signing import SigningKey

TEST_DB_PATH = Path("/tmp/tee_inference_api_test.sqlite")
TEST_DB_PATH.unlink(missing_ok=True)

# A fixed platform provisioner Ed25519 seed + a generated Fernet key so every
# test process agrees on the same provisioning key material.
PLATFORM_PROVISIONER_SEED = bytes(range(32))
PLATFORM_PROVISIONER_SIGNING_KEY = SigningKey(PLATFORM_PROVISIONER_SEED)
PLATFORM_PROVISIONER_VERIFY_KEY_HEX = bytes(
    PLATFORM_PROVISIONER_SIGNING_KEY.verify_key
).hex()
TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()

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
        # --- SERVING / COORDINATION plane test config --------------------- #
        "PROVISIONER_ENABLED": "false",
        "CHAIN_REQUIRED": "false",
        "ALLOW_DEV_ATTESTATION": "true",
        "REGISTRATION_KEY_TYPE": "ed25519",
        "TLS_PIN_ENFORCE": "false",
        "PROVISIONER_SIGNING_KEY_HEX": PLATFORM_PROVISIONER_SEED.hex(),
        "PROVISIONER_VERIFY_KEY_HEX": PLATFORM_PROVISIONER_VERIFY_KEY_HEX,
        "PROVISIONER_TOKEN_ENCRYPTION_KEY": TOKEN_ENCRYPTION_KEY,
    }
)

from inference_api.crypto import encrypt_token  # noqa: E402
from inference_api.db import async_session_maker, engine  # noqa: E402
from inference_api.main import create_app  # noqa: E402
from inference_api.models import (  # noqa: E402
    APIKey,
    Base,
    ManualTokenAdjustment,
    Miner,
    MinerModel,
    ProvisionedToken,
    ServiceClient,
    ServiceClientRole,
    User,
)
from inference_api.security import expires_in, keyed_hash, utcnow  # noqa: E402


@dataclass(frozen=True)
class SeededAPIKey:
    raw_key: str
    user_id: uuid.UUID
    api_key_id: uuid.UUID


@dataclass(frozen=True)
class SeededRouterToken:
    raw_token: str
    router_client_id: uuid.UUID


@dataclass(frozen=True)
class SeededMinerToken:
    raw_token: str
    miner_client_id: uuid.UUID


@dataclass(frozen=True)
class SeededMiner:
    miner_id: uuid.UUID
    hotkey: str
    model_ids: list[str] = field(default_factory=list)


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


@pytest_asyncio.fixture
async def seed_miner_token():
    async def factory(
        *,
        raw_token: str = "rk_live_miner",
        expires_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> SeededMinerToken:
        miner_client = ServiceClient(
            id=uuid.uuid4(),
            role=ServiceClientRole.miner,
            hashed_token=keyed_hash(raw_token),
            rate_limit_per_minute=600,
            expires_at=expires_at,
            revoked_at=revoked_at,
        )
        async with async_session_maker() as session:
            session.add(miner_client)
            await session.commit()
        return SeededMinerToken(raw_token=raw_token, miner_client_id=miner_client.id)

    return factory


@pytest_asyncio.fixture
async def seed_miner():
    async def factory(
        *,
        hotkey: str | None = None,
        tee_endpoint: str = "http://miner.test",
        model_ids: list[str] | None = None,
        attestation_status: str = "attested",
        health: str = "healthy",
        tls_cert_fingerprint: str | None = "a" * 64,
        enclave_verify_key: str | None = "b" * 64,
        capacity: dict | None = None,
        model_hash: str | None = None,
    ) -> SeededMiner:
        hotkey = (hotkey or SigningKey.generate().verify_key.encode().hex()).lower()
        model_ids = model_ids if model_ids is not None else ["demo-chat-001"]
        miner = Miner(
            id=uuid.uuid4(),
            hotkey=hotkey,
            tee_endpoint=tee_endpoint,
            attestation_status=attestation_status,
            attestation_mode="dev",
            health=health,
            tls_cert_fingerprint=tls_cert_fingerprint,
            enclave_verify_key=enclave_verify_key,
            capacity=capacity or {"available_concurrent_requests": 4, "queue_depth": 0},
            last_seen=utcnow(),
            registered_at=utcnow(),
        )
        async with async_session_maker() as session:
            session.add(miner)
            for model_id in model_ids:
                session.add(
                    MinerModel(
                        id=uuid.uuid4(),
                        miner_id=miner.id,
                        model_id=model_id,
                        model_hash=model_hash,
                        attestation_status=attestation_status,
                        loaded=True,
                        last_advertised_at=utcnow(),
                    )
                )
            await session.commit()
        return SeededMiner(miner_id=miner.id, hotkey=hotkey, model_ids=list(model_ids))

    return factory


@pytest_asyncio.fixture
async def seed_provisioned_token():
    async def factory(
        *,
        miner_id: uuid.UUID,
        model_id: str | None = None,
        raw_token: str = "inf_seeded_token",
        admin_token: str = "adm_seeded_token",
        key_id: str = "platform-default",
        status: str = "active",
        expires_at: datetime | None = None,
    ) -> ProvisionedToken:
        token = ProvisionedToken(
            id=uuid.uuid4(),
            miner_id=miner_id,
            model_id=model_id,
            key_id=key_id,
            encrypted_token=encrypt_token(raw_token),
            admin_encrypted_token=encrypt_token(admin_token),
            status=status,
            provisioned_at=utcnow(),
            expires_at=expires_at or expires_in(seconds=86_400),
        )
        async with async_session_maker() as session:
            session.add(token)
            await session.commit()
        return token

    return factory
