"""Regression tests for D2: unbounded `full_name` reaches Postgres and 500s.

`full_name` on `UserCreate`/`UserUpdate` (app/users/schemas.py) had no length
constraint, while `users.full_name` is `VARCHAR(255)` (app/db/models/user.py).
An oversized value therefore passed pydantic validation and reached Postgres,
which raised an unhandled `StringDataRightTruncationError` -- reproduced live
and UNAUTHENTICATED via `POST /auth/register` with a 3,000,000-character
`full_name`, returning HTTP 500 with a full traceback (filesystem paths,
SQLAlchemy internals, the raw INSERT statement).

These tests assert the value is rejected as a clean validation error (422)
before it ever reaches the database, on both the create and update schemas,
and via the real unauthenticated `/auth/register` HTTP path.
"""

import pytest
from pydantic import ValidationError

from app.users.schemas import FULL_NAME_MAX_LENGTH, UserCreate, UserUpdate


def test_user_create_rejects_oversized_full_name():
    with pytest.raises(ValidationError) as exc_info:
        UserCreate(
            email="oversized@example.com",
            password="a-strong-password",
            full_name="x" * 3_000_000,
        )
    assert "full_name" in str(exc_info.value)


def test_user_update_rejects_oversized_full_name():
    with pytest.raises(ValidationError) as exc_info:
        UserUpdate(full_name="x" * 3_000_000)
    assert "full_name" in str(exc_info.value)


def test_user_create_accepts_full_name_at_column_limit():
    # Exactly the DB column width (255) must still be accepted.
    user = UserCreate(
        email="fits-exactly@example.com",
        password="a-strong-password",
        full_name="x" * FULL_NAME_MAX_LENGTH,
    )
    assert len(user.full_name) == FULL_NAME_MAX_LENGTH


def test_user_create_rejects_one_over_column_limit():
    with pytest.raises(ValidationError):
        UserCreate(
            email="one-over@example.com",
            password="a-strong-password",
            full_name="x" * (FULL_NAME_MAX_LENGTH + 1),
        )


async def test_register_endpoint_rejects_oversized_full_name_with_422_not_500(client):
    """End-to-end reproduction of the live finding: an unauthenticated
    oversized payload to /auth/register must yield a clean 422, never a 500
    with a leaked traceback.
    """
    response = await client.post(
        "/auth/register",
        json={
            "email": "oversized-e2e@example.com",
            "password": "a-strong-password",
            "full_name": "x" * 3_000_000,
        },
    )
    assert response.status_code == 422
    body = response.text
    assert "Traceback" not in body
    assert "StringDataRightTruncationError" not in body
