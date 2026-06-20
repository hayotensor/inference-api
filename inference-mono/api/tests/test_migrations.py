import os
import subprocess
import sys

import pytest

POSTGRES_TEST_DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not POSTGRES_TEST_DATABASE_URL,
    reason="Set POSTGRES_TEST_DATABASE_URL to run Postgres-backed Alembic checks",
)
def test_postgres_alembic_upgrade_head_and_model_drift_check():
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DEBUG": "false",
        "DATABASE_URL": POSTGRES_TEST_DATABASE_URL or "",
        "JWT_SECRET": "test-jwt-secret-with-enough-entropy",
        "VERIFICATION_TOKEN_SECRET": "test-verification-secret",
        "RESET_PASSWORD_TOKEN_SECRET": "test-reset-secret",
        "OAUTH_STATE_SECRET": "test-oauth-secret",
        "SESSION_SECRET": "test-session-secret",
        "SECRET_PEPPER": "test-pepper-secret",
        "RATE_LIMIT_STORAGE_URL": "memory://",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "check"], env=env, check=True)
