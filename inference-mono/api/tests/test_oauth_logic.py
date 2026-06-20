from types import SimpleNamespace

from fastapi_users.db import SQLAlchemyUserDatabase
from pytest import raises
from sqlalchemy import select

from app.auth.oauth import OAuthProfile, OAuthService, extract_oauth_profile, parse_oauth_email_verified
from app.auth.service import AuthService, UserManager
from app.db.models.oauth_account import OAuthAccount
from app.db.models.user import User
from app.db.session import async_session_maker


class DummyClient:
    host = "testclient"


class DummyURL:
    path = "/auth/oauth"


def dummy_request():
    return SimpleNamespace(client=DummyClient(), headers={}, url=DummyURL(), method="GET")


async def test_oauth_verified_email_links_provider_accounts_to_existing_user():
    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        user_manager = UserManager(user_db)
        auth_service = AuthService(session, user_manager)
        oauth_service = OAuthService(session, auth_service)

        google = OAuthProfile(
            provider="google",
            account_id="google-123",
            email="oauth@example.com",
            email_verified=True,
            full_name="OAuth User",
        )
        google_result = await oauth_service.login_or_create(
            google,
            {"access_token": "google-token", "expires_at": 9999999999},
            dummy_request(),
        )
        await session.commit()

        apple = OAuthProfile(
            provider="apple",
            account_id="apple-456",
            email="oauth@example.com",
            email_verified=True,
            full_name="OAuth User",
        )
        apple_result = await oauth_service.login_or_create(
            apple,
            {"access_token": "apple-token", "expires_at": 9999999999},
            dummy_request(),
        )
        await session.commit()

        assert google_result.user.id == apple_result.user.id
        users = (await session.execute(select(User))).scalars().all()
        accounts = (await session.execute(select(OAuthAccount))).scalars().all()
        assert len(users) == 1
        assert len(accounts) == 2


async def test_oauth_email_verified_string_false_is_not_trusted():
    profile = await extract_oauth_profile(
        "google",
        dummy_request(),
        {"userinfo": {"sub": "google-unverified", "email": "unverified@example.com", "email_verified": "false"}},
    )
    assert profile.email_verified is False
    assert parse_oauth_email_verified("true") is True
    assert parse_oauth_email_verified("false") is False


async def test_oauth_unverified_email_is_rejected():
    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        user_manager = UserManager(user_db)
        auth_service = AuthService(session, user_manager)
        oauth_service = OAuthService(session, auth_service)

        with raises(Exception) as exc_info:
            await oauth_service.login_or_create(
                OAuthProfile(
                    provider="google",
                    account_id="google-unverified",
                    email="unverified@example.com",
                    email_verified=False,
                    full_name="Unverified User",
                ),
                {"access_token": "google-token", "expires_at": 9999999999},
                dummy_request(),
            )
        assert getattr(exc_info.value, "status_code", None) == 400


async def test_oauth_unverified_existing_account_is_rejected():
    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        user_manager = UserManager(user_db)
        auth_service = AuthService(session, user_manager)
        oauth_service = OAuthService(session, auth_service)

        await oauth_service.login_or_create(
            OAuthProfile(
                provider="google",
                account_id="google-existing",
                email="existing-oauth@example.com",
                email_verified=True,
                full_name="Existing OAuth",
            ),
            {"access_token": "google-token", "expires_at": 9999999999},
            dummy_request(),
        )
        await session.commit()

        with raises(Exception) as exc_info:
            await oauth_service.login_or_create(
                OAuthProfile(
                    provider="google",
                    account_id="google-existing",
                    email="existing-oauth@example.com",
                    email_verified=False,
                    full_name="Existing OAuth",
                ),
                {"access_token": "google-token", "expires_at": 9999999999},
                dummy_request(),
            )
        assert getattr(exc_info.value, "status_code", None) == 400
