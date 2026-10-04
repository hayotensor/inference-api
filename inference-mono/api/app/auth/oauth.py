from dataclasses import dataclass
from typing import Any, Literal

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from fastapi_users.exceptions import UserNotExists
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.schemas import TokenResponse
from app.auth.service import AuthService
from app.core.config import settings
from app.core.errors import bad_request, unauthorized
from app.db.models.oauth_account import OAuthAccount
from app.db.models.user import User
from app.users.schemas import UserRead

oauth = OAuth()


def configure_oauth_clients() -> None:
    if settings.google_client_id and settings.google_client_secret:
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    if settings.apple_client_id and settings.apple_client_secret:
        oauth.register(
            name="apple",
            client_id=settings.apple_client_id,
            client_secret=settings.apple_client_secret.get_secret_value(),
            server_metadata_url="https://appleid.apple.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email name"},
        )


configure_oauth_clients()


@dataclass(frozen=True)
class OAuthProfile:
    provider: Literal["google", "apple"]
    account_id: str
    email: str
    email_verified: bool
    full_name: str | None = None


def parse_oauth_email_verified(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def get_oauth_client(provider: Literal["google", "apple"]):
    client = oauth.create_client(provider)
    if client is None:
        raise bad_request(
            "oauth_provider_not_configured",
            f"{provider.title()} OAuth is not configured",
        )
    return client


async def extract_oauth_profile(
    provider: Literal["google", "apple"],
    request: Request,
    token: dict[str, Any],
) -> OAuthProfile:
    userinfo = token.get("userinfo")
    if not userinfo:
        client = get_oauth_client(provider)
        userinfo = await client.parse_id_token(request, token)
    account_id = str(userinfo.get("sub") or userinfo.get("id") or "")
    email = str(userinfo.get("email") or "")
    if not account_id or not email:
        raise unauthorized("OAuth provider did not return a usable identity")
    email_verified = parse_oauth_email_verified(userinfo.get("email_verified"))
    full_name = userinfo.get("name")
    if not full_name and userinfo.get("given_name"):
        full_name = " ".join(
            part for part in [userinfo.get("given_name"), userinfo.get("family_name")] if part
        )
    return OAuthProfile(
        provider=provider,
        account_id=account_id,
        email=email.lower(),
        email_verified=email_verified,
        full_name=full_name,
    )


class OAuthService:
    def __init__(self, session: AsyncSession, auth_service: AuthService) -> None:
        self.session = session
        self.auth_service = auth_service

    async def login_or_create(
        self,
        profile: OAuthProfile,
        token: dict[str, Any],
        request: Request,
    ) -> TokenResponse:
        if not profile.email_verified:
            raise bad_request(
                "oauth_email_unverified",
                "OAuth provider did not verify this email address",
            )
        result = await self.session.execute(
            select(OAuthAccount).where(
                OAuthAccount.oauth_name == profile.provider,
                OAuthAccount.account_id == profile.account_id,
            )
        )
        account = result.scalar_one_or_none()
        if account is not None:
            user = await self.session.get(User, account.user_id)
            if user is None or not user.is_active:
                raise unauthorized("OAuth account is no longer active")
            self._update_oauth_account(account, token)
        else:
            user = await self._find_or_create_user(profile)
            account = OAuthAccount(
                oauth_name=profile.provider,
                access_token=token.get("access_token") or "",
                expires_at=token.get("expires_at"),
                refresh_token=token.get("refresh_token"),
                account_id=profile.account_id,
                account_email=profile.email,
                user_id=user.id,
            )
            # Appending (not just session.add) keeps `user.oauth_accounts` in
            # sync in-memory -- the cascade on that relationship still adds
            # `account` to the session. This matters because `user` is a
            # freshly-created object here: without the append, the
            # `UserRead.model_validate(user)` call below reads a still-empty
            # `oauth_accounts` collection, so a brand-new Google sign-in's own
            # login response reports `oauth_providers: []` and the frontend
            # wrongly shows password-reset UI for a provider-integrated account.
            user.oauth_accounts.append(account)

        # `email_verified` was already required to be true above, on every
        # path through this method (new user, first-time link to an
        # existing account, or a returning already-linked login) -- so the
        # provider vouches for this address regardless of which branch got
        # here. `_find_or_create_user` only sets this for a brand-new user;
        # an existing account (e.g. registered by email/password and never
        # verified, then signed in with Google using the same address) fell
        # through untouched, and once linked, every later login took the
        # `account is not None` branch above which never touched it either
        # -- so it never self-healed. Guard the write so an already-verified
        # user doesn't take a needless UPDATE (and bump `updated_at`) on
        # every login.
        if not user.is_verified:
            user.is_verified = True
            self.session.add(user)

        access_token, refresh_token = await self.auth_service.issue_token_pair(user, request)
        await write_audit_log(
            self.session,
            f"auth.oauth_{profile.provider}",
            user_id=user.id,
            request=request,
            metadata={"account_id": profile.account_id, "email": profile.email},
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.jwt_lifetime_seconds,
            user=UserRead.model_validate(user),
        )

    async def _find_or_create_user(self, profile: OAuthProfile) -> User:
        user: User | None = None
        try:
            user = await self.auth_service.user_manager.get_by_email(profile.email)
        except UserNotExists:
            user = None
        if user is None:
            user = await self.auth_service.create_random_password_user(
                profile.email,
                full_name=profile.full_name,
            )
            # is_verified is set once, uniformly for every path (new user,
            # first link, or returning login), in login_or_create below.
            self.session.add(user)
        return user

    @staticmethod
    def _update_oauth_account(account: OAuthAccount, token: dict[str, Any]) -> None:
        account.access_token = token.get("access_token") or account.access_token
        account.refresh_token = token.get("refresh_token") or account.refresh_token
        account.expires_at = token.get("expires_at") or account.expires_at
