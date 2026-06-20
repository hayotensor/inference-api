from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi_users.exceptions import (
    InvalidPasswordException,
    InvalidResetPasswordToken,
    InvalidVerifyToken,
    UserAlreadyExists,
    UserAlreadyVerified,
    UserNotExists,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.oauth import OAuthService, extract_oauth_profile, get_oauth_client
from app.auth.phone import (
    PhoneAuthResponse,
    PhoneLinkRequest,
    PhoneOTPResponse,
    PhoneRequestOTP,
    PhoneService,
    PhoneVerifyOTP,
)
from app.auth.schemas import (
    EmailPasswordLogin,
    ForgotPasswordRequest,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    RequestVerifyEmail,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.auth.service import AuthService, UserManager, get_user_manager, optional_active_user
from app.auth.token_revocation import extract_bearer_token
from app.core.config import settings
from app.core.errors import bad_request, unauthorized
from app.core.rate_limit import limiter
from app.core.security import is_synthetic_phone_email
from app.db.models.phone_verification_code import PhoneVerificationPurpose
from app.db.models.user import User
from app.db.session import get_async_session
from app.users.schemas import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


async def get_auth_service(
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
) -> AuthService:
    return AuthService(session, user_manager)


async def get_phone_service(
    session: AsyncSession = Depends(get_async_session),
    auth_service: AuthService = Depends(get_auth_service),
) -> PhoneService:
    return PhoneService(session, auth_service)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    payload: UserCreate,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
) -> User:
    try:
        user = await user_manager.create(payload, safe=True, request=request)
    except UserAlreadyExists as exc:
        raise bad_request("user_exists", "A user with this email already exists") from exc
    except InvalidPasswordException as exc:
        raise bad_request("invalid_password", exc.reason) from exc

    await write_audit_log(session, "auth.register", user_id=user.id, request=request)
    await session.commit()
    try:
        await user_manager.request_verify(user, request)
    except UserAlreadyVerified:
        pass
    return user


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    payload: EmailPasswordLogin,
    session: AsyncSession = Depends(get_async_session),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    user = await auth_service.authenticate_email(payload.email, payload.password)
    if user is None:
        raise unauthorized("Invalid email or password")
    if not user.is_active:
        raise unauthorized("User is inactive")
    access_token, refresh_token = await auth_service.issue_token_pair(user, request)
    await write_audit_log(session, "auth.login", user_id=user.id, request=request)
    await session.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_lifetime_seconds,
        user=UserRead.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_async_session),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    user, access_token, refresh_token = await auth_service.rotate_refresh_token(
        payload.refresh_token, request
    )
    await write_audit_log(session, "auth.refresh", user_id=user.id, request=request)
    await session.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_lifetime_seconds,
        user=UserRead.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def logout(
    request: Request,
    payload: LogoutRequest,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
    auth_service: AuthService = Depends(get_auth_service),
    user: User | None = Depends(optional_active_user),
) -> Response:
    access_token = extract_bearer_token(request)
    if access_token:
        await auth_service.revoke_access_token(access_token)
    if payload.refresh_token:
        await auth_service.revoke_refresh_token(payload.refresh_token, user.id if user else None)
    if user:
        await write_audit_log(session, "auth.logout", user_id=user.id, request=request)
    await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/request-verify-email", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
async def request_verify_email(
    request: Request,
    payload: RequestVerifyEmail,
    user_manager: UserManager = Depends(get_user_manager),
) -> MessageResponse:
    if is_synthetic_phone_email(str(payload.email)):
        return MessageResponse(detail="If the account exists, a verification email has been sent")
    try:
        user = await user_manager.get_by_email(payload.email)
        await user_manager.request_verify(user, request)
    except (UserNotExists, UserAlreadyVerified):
        pass
    return MessageResponse(detail="If the account exists, a verification email has been sent")


@router.post("/verify-email", response_model=UserRead)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    payload: VerifyEmailRequest,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
) -> User:
    try:
        user = await user_manager.verify(payload.token, request)
    except (InvalidVerifyToken, UserAlreadyVerified) as exc:
        raise bad_request("invalid_verification_token", "Invalid verification token") from exc
    await write_audit_log(session, "auth.email_verify", user_id=user.id, request=request)
    await session.commit()
    return user


@router.post("/forgot-password", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    user_manager: UserManager = Depends(get_user_manager),
) -> MessageResponse:
    try:
        user = await user_manager.get_by_email(payload.email)
        await user_manager.forgot_password(user, request)
    except UserNotExists:
        pass
    return MessageResponse(detail="If the account exists, a password reset email has been sent")


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("10/minute")
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    session: AsyncSession = Depends(get_async_session),
    user_manager: UserManager = Depends(get_user_manager),
) -> MessageResponse:
    try:
        user = await user_manager.reset_password(payload.token, payload.password, request)
    except (InvalidResetPasswordToken, InvalidPasswordException) as exc:
        reason = getattr(exc, "reason", "Invalid reset token")
        raise bad_request("invalid_reset_token", reason) from exc
    await write_audit_log(session, "auth.password_reset", user_id=user.id, request=request)
    await session.commit()
    return MessageResponse(detail="Password has been reset")


async def _oauth_login(request: Request, provider: Literal["google", "apple"]):
    client = get_oauth_client(provider)
    redirect_uri = request.url_for(f"{provider}_callback")
    return await client.authorize_redirect(request, redirect_uri)


async def _oauth_callback(
    request: Request,
    provider: Literal["google", "apple"],
    session: AsyncSession,
    auth_service: AuthService,
) -> TokenResponse:
    client = get_oauth_client(provider)
    token = await client.authorize_access_token(request)
    profile = await extract_oauth_profile(provider, request, token)
    result = await OAuthService(session, auth_service).login_or_create(profile, token, request)
    await session.commit()
    return result


@router.get("/google/login")
@limiter.limit("10/minute")
async def google_login(request: Request):
    return await _oauth_login(request, "google")


@router.get("/google/callback", response_model=TokenResponse, name="google_callback")
@limiter.limit("20/minute")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return await _oauth_callback(request, "google", session, auth_service)


@router.get("/apple/login")
@limiter.limit("10/minute")
async def apple_login(request: Request):
    return await _oauth_login(request, "apple")


@router.get("/apple/callback", response_model=TokenResponse, name="apple_callback")
@limiter.limit("20/minute")
async def apple_callback(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    return await _oauth_callback(request, "apple", session, auth_service)


@router.post("/phone/request-otp", response_model=PhoneOTPResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("3/minute")
async def phone_request_otp(
    request: Request,
    payload: PhoneRequestOTP,
    session: AsyncSession = Depends(get_async_session),
    phone_service: PhoneService = Depends(get_phone_service),
    user: User | None = Depends(optional_active_user),
) -> PhoneOTPResponse:
    if payload.purpose == PhoneVerificationPurpose.link and user is None:
        raise unauthorized("Login is required to request a phone-link OTP")
    normalized = await phone_service.request_otp(
        payload.phone_number, payload.purpose, request, user=user
    )
    await write_audit_log(
        session,
        f"auth.phone_otp_request.{payload.purpose.value}",
        user_id=user.id if user else None,
        request=request,
        metadata={"phone_number": normalized},
    )
    await session.commit()
    return PhoneOTPResponse(detail="Verification code sent", expires_in=settings.otp_ttl_seconds)


@router.post("/phone/verify-otp", response_model=PhoneAuthResponse)
@limiter.limit("10/minute")
async def phone_verify_otp(
    request: Request,
    payload: PhoneVerifyOTP,
    session: AsyncSession = Depends(get_async_session),
    phone_service: PhoneService = Depends(get_phone_service),
) -> PhoneAuthResponse:
    result = await phone_service.login_or_create(payload, request)
    await session.commit()
    return result


@router.post("/phone/link", response_model=UserRead)
@limiter.limit("10/minute")
async def phone_link(
    request: Request,
    payload: PhoneLinkRequest,
    session: AsyncSession = Depends(get_async_session),
    phone_service: PhoneService = Depends(get_phone_service),
    user: User = Depends(optional_active_user),
) -> User:
    if user is None:
        raise unauthorized("Login is required to link a phone number")
    updated = await phone_service.link_phone(user, payload, request)
    await session.commit()
    return updated


@router.post("/phone/unlink", response_model=UserRead)
@limiter.limit("10/minute")
async def phone_unlink(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    phone_service: PhoneService = Depends(get_phone_service),
    user: User = Depends(optional_active_user),
) -> User:
    if user is None:
        raise unauthorized("Login is required to unlink a phone number")
    updated = await phone_service.unlink_phone(user, request)
    await session.commit()
    return updated
