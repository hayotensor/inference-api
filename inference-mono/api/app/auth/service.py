import logging
import secrets
import uuid
from types import SimpleNamespace

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.jwt import decode_jwt, generate_jwt
from jwt import PyJWTError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.token_revocation import is_jti_revoked, revoke_access_token
from app.core.config import settings
from app.core.errors import unauthorized
from app.core.security import (
    constant_time_equal,
    expires_in,
    generate_refresh_token,
    is_past,
    keyed_hash,
    utcnow,
)
from app.db.models.email_verification_token import EmailVerificationToken
from app.db.models.oauth_account import OAuthAccount
from app.db.models.password_reset_token import PasswordResetToken
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from app.db.session import get_async_session
from app.email.service import EmailService

logger = logging.getLogger(__name__)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.reset_password_token_secret.get_secret_value()
    reset_password_token_lifetime_seconds = 3600
    verification_token_secret = settings.verification_token_secret.get_secret_value()
    verification_token_lifetime_seconds = 3600

    async def validate_password(self, password: str, user) -> None:
        if len(password) < 8:
            from fastapi_users import InvalidPasswordException

            raise InvalidPasswordException(reason="Password must be at least 8 characters")
        email = getattr(user, "email", None)
        if email and email.lower() in password.lower():
            from fastapi_users import InvalidPasswordException

            raise InvalidPasswordException(reason="Password must not contain the email address")

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("auth.registered", extra={"user_id": str(user.id)})

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        session = getattr(self.user_db, "session", None)
        if session is not None:
            session.add(
                EmailVerificationToken(
                    user_id=user.id,
                    token_hash=keyed_hash(token),
                    expires_at=expires_in(seconds=self.verification_token_lifetime_seconds),
                )
            )
            await session.commit()
        await EmailService().send_verification_email(user.email, token)

    async def on_after_verify(self, user: User, request: Request | None = None) -> None:
        session = getattr(self.user_db, "session", None)
        if session is not None:
            await session.execute(
                update(EmailVerificationToken)
                .where(
                    EmailVerificationToken.user_id == user.id,
                    EmailVerificationToken.consumed_at.is_(None),
                )
                .values(consumed_at=utcnow())
            )
            await session.commit()
        logger.info("auth.email_verified", extra={"user_id": str(user.id)})

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        session = getattr(self.user_db, "session", None)
        if session is not None:
            session.add(
                PasswordResetToken(
                    user_id=user.id,
                    token_hash=keyed_hash(token),
                    expires_at=expires_in(seconds=self.reset_password_token_lifetime_seconds),
                )
            )
            await session.commit()
        await EmailService().send_password_reset_email(user.email, token)

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        session = getattr(self.user_db, "session", None)
        if session is not None:
            now = utcnow()
            await session.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            await session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            user.token_version += 1
            session.add(user)
            await session.commit()
        logger.info("auth.password_reset", extra={"user_id": str(user.id)})


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> SQLAlchemyUserDatabase:
    yield SQLAlchemyUserDatabase(session, User, OAuthAccount)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> UserManager:
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="/auth/login")


class RevocableJWTStrategy(JWTStrategy):
    async def write_token(self, user: User) -> str:
        data = {
            "sub": str(user.id),
            "aud": [settings.jwt_audience],
            "jti": str(uuid.uuid4()),
            "token_version": user.token_version,
        }
        return generate_jwt(
            data,
            self.encode_key,
            self.lifetime_seconds,
            algorithm=self.algorithm,
        )

    async def read_token(self, token: str | None, user_manager: UserManager) -> User | None:
        if token is None:
            return None
        try:
            data = decode_jwt(
                token,
                self.decode_key,
                self.token_audience,
                algorithms=[self.algorithm],
            )
        except PyJWTError:
            return None

        jti = data.get("jti")
        if not jti or await is_jti_revoked(str(jti)):
            return None

        user_id = data.get("sub")
        if user_id is None:
            return None
        try:
            parsed_id = user_manager.parse_id(user_id)
            user = await user_manager.get(parsed_id)
        except Exception:
            return None

        token_version = data.get("token_version")
        try:
            parsed_token_version = int(token_version)
        except (TypeError, ValueError):
            return None
        if parsed_token_version != user.token_version:
            return None
        return user


def get_jwt_strategy() -> RevocableJWTStrategy:
    return RevocableJWTStrategy(
        secret=settings.jwt_secret.get_secret_value(),
        token_audience=[settings.jwt_audience],
        lifetime_seconds=settings.jwt_lifetime_seconds,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])
current_active_user = fastapi_users.current_user(active=True)
current_verified_email_user = fastapi_users.current_user(active=True, verified=True)
optional_active_user = fastapi_users.current_user(active=True, optional=True)


class AuthService:
    def __init__(self, session: AsyncSession, user_manager: UserManager) -> None:
        self.session = session
        self.user_manager = user_manager

    async def authenticate_email(self, email: str, password: str) -> User | None:
        credentials = SimpleNamespace(username=email, password=password)
        return await self.user_manager.authenticate(credentials)

    async def issue_access_token(self, user: User) -> str:
        strategy = get_jwt_strategy()
        return await strategy.write_token(user)

    async def create_refresh_token(
        self,
        user: User,
        request: Request | None,
        *,
        family_id: uuid.UUID | None = None,
    ) -> tuple[str, RefreshToken]:
        raw_token = generate_refresh_token()
        db_token = RefreshToken(
            user_id=user.id,
            token_hash=keyed_hash(raw_token),
            family_id=family_id or uuid.uuid4(),
            expires_at=expires_in(days=settings.refresh_token_lifetime_days),
            created_by_ip=request.client.host if request and request.client else None,
            user_agent=request.headers.get("user-agent") if request else None,
        )
        self.session.add(db_token)
        await self.session.flush()
        return raw_token, db_token

    async def issue_token_pair(self, user: User, request: Request | None) -> tuple[str, str]:
        access_token = await self.issue_access_token(user)
        refresh_token, _ = await self.create_refresh_token(user, request)
        return access_token, refresh_token

    async def rotate_refresh_token(self, raw_token: str, request: Request | None) -> tuple[User, str, str]:
        token_hash = keyed_hash(raw_token)
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        db_token = result.scalar_one_or_none()
        if db_token is None:
            raise unauthorized("Invalid refresh token")
        if not constant_time_equal(db_token.token_hash, token_hash):
            raise unauthorized("Invalid refresh token")
        if db_token.revoked_at is not None or is_past(db_token.expires_at):
            await self.revoke_refresh_token_family(db_token.family_id, commit=True)
            raise unauthorized("Refresh token is no longer valid")
        user = await self.session.get(User, db_token.user_id)
        if user is None or not user.is_active:
            raise unauthorized("User is no longer active")

        now = utcnow()
        update_result = await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.id == db_token.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        if update_result.rowcount != 1:
            await self.revoke_refresh_token_family(db_token.family_id, commit=True)
            raise unauthorized("Refresh token is no longer valid")

        new_raw_token, new_db_token = await self.create_refresh_token(
            user, request, family_id=db_token.family_id
        )
        db_token.replaced_by_id = new_db_token.id
        access_token = await self.issue_access_token(user)
        return user, access_token, new_raw_token

    async def revoke_access_token(self, raw_token: str) -> bool:
        return await revoke_access_token(raw_token)

    async def revoke_refresh_token(self, raw_token: str, user_id: uuid.UUID | None = None) -> bool:
        token_hash = keyed_hash(raw_token)
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if user_id is not None:
            statement = statement.where(RefreshToken.user_id == user_id)
        result = await self.session.execute(statement)
        db_token = result.scalar_one_or_none()
        if db_token is None or db_token.revoked_at is not None:
            return False
        db_token.revoked_at = utcnow()
        return True

    async def revoke_refresh_token_family(self, family_id: uuid.UUID, *, commit: bool = False) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        if commit:
            await self.session.commit()

    async def create_random_password_user(self, email: str, *, full_name: str | None = None) -> User:
        from app.users.schemas import UserCreate

        password = secrets.token_urlsafe(32)
        return await self.user_manager.create(
            UserCreate(email=email, password=password, full_name=full_name),
            safe=True,
        )
