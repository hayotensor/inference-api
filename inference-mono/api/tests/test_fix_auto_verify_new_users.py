"""`AUTO_VERIFY_NEW_USERS` lets a dev/demo signup skip the email round-trip.

Registration normally sends a verification email and leaves the account
unverified, which blocks API-key creation (`app/api_keys/service.py` requires a
verified email, phone, or wallet). With no real mailbox, that forces the operator
to dig the token out of the console log. This flag marks the account verified at
registration and sends nothing.

It is OFF by default because it makes "verified email" meaningless: anyone could
register an address they do not control and have it treated as proven.
"""

import pytest

from app.core.config import settings


@pytest.fixture
def auto_verify_on(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", True)


@pytest.fixture
def auto_verify_off(monkeypatch):
    monkeypatch.setattr(settings, "auto_verify_new_users", False)


def test_defaults_to_off():
    """The insecure-but-convenient behavior must never be the default."""
    from app.core.config import Settings

    assert Settings().auto_verify_new_users is False


async def test_registration_is_unverified_when_flag_off(client, auto_verify_off):
    response = await client.post(
        "/auth/register",
        json={"email": "flagoff@example.com", "password": "FlagOffPassw0rd!1", "full_name": "Off"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_verified"] is False


async def test_registration_is_verified_when_flag_on(client, auto_verify_on):
    response = await client.post(
        "/auth/register",
        json={"email": "flagon@example.com", "password": "FlagOnPassw0rd!1", "full_name": "On"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_verified"] is True


async def test_verified_signup_can_immediately_create_an_api_key(client, auto_verify_on):
    """The point of the flag: no email round-trip before the account is usable."""
    email, password = "flagkey@example.com", "FlagKeyPassw0rd!1"
    register = await client.post(
        "/auth/register", json={"email": email, "password": password, "full_name": "Key"}
    )
    assert register.status_code == 201, register.text

    login = await client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    created = await client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "immediate", "environment": "test", "scopes": ["models:read"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["key"].startswith("sk_test_")
