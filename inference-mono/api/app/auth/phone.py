from typing import Literal

from fastapi import Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.schemas import TokenResponse
from app.auth.service import AuthService
from app.core.config import settings
from app.core.errors import bad_request, forbidden, not_found, unauthorized
from app.core.security import (
    constant_time_equal,
    expires_in,
    generate_otp_code,
    hash_otp,
    normalize_phone_number,
    synthetic_phone_email,
    utcnow,
)
from app.db.models.phone_verification_code import PhoneVerificationCode, PhoneVerificationPurpose
from app.db.models.user import User
from app.sms.service import SMSService
from app.users.schemas import UserRead


class PhoneRequestOTP(BaseModel):
    phone_number: str
    purpose: PhoneVerificationPurpose = PhoneVerificationPurpose.login


class PhoneVerifyOTP(BaseModel):
    phone_number: str
    code: str = Field(min_length=6, max_length=6)
    email: EmailStr | None = None
    full_name: str | None = None


class PhoneLinkRequest(BaseModel):
    phone_number: str
    code: str = Field(min_length=6, max_length=6)


class PhoneOTPResponse(BaseModel):
    detail: str
    expires_in: int


class PhoneAuthResponse(TokenResponse):
    auth_method: Literal["phone"] = "phone"


class PhoneService:
    def __init__(self, session: AsyncSession, auth_service: AuthService) -> None:
        self.session = session
        self.auth_service = auth_service

    async def request_otp(
        self,
        phone_number: str,
        purpose: PhoneVerificationPurpose,
        request: Request,
        *,
        user: User | None = None,
    ) -> str:
        normalized = self._normalize_phone_or_400(phone_number)
        await self.session.execute(
            update(PhoneVerificationCode)
            .where(
                PhoneVerificationCode.phone_number == normalized,
                PhoneVerificationCode.purpose == purpose,
                PhoneVerificationCode.consumed_at.is_(None),
            )
            .values(consumed_at=utcnow())
        )
        code = generate_otp_code()
        self.session.add(
            PhoneVerificationCode(
                user_id=user.id if user else None,
                phone_number=normalized,
                purpose=purpose,
                otp_hash=hash_otp(normalized, code, purpose.value),
                expires_at=expires_in(seconds=settings.otp_ttl_seconds),
                request_ip=request.client.host if request.client else None,
            )
        )
        await SMSService().send_otp(normalized, code)
        return normalized

    async def verify_code(
        self, phone_number: str, code: str, purpose: PhoneVerificationPurpose
    ) -> str:
        normalized = self._normalize_phone_or_400(phone_number)
        result = await self.session.execute(
            select(PhoneVerificationCode)
            .where(
                PhoneVerificationCode.phone_number == normalized,
                PhoneVerificationCode.purpose == purpose,
                PhoneVerificationCode.consumed_at.is_(None),
                PhoneVerificationCode.expires_at > utcnow(),
            )
            .order_by(PhoneVerificationCode.created_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise bad_request("otp_invalid", "Invalid or expired verification code")
        expected = hash_otp(normalized, code, purpose.value)
        record.attempts += 1
        if not constant_time_equal(record.otp_hash, expected):
            if record.attempts >= settings.otp_max_attempts:
                record.consumed_at = utcnow()
                await self.session.commit()
                raise bad_request("otp_attempts_exceeded", "Too many verification attempts")
            await self.session.commit()
            raise bad_request("otp_invalid", "Invalid or expired verification code")
        record.consumed_at = utcnow()
        return normalized

    async def login_or_create(
        self,
        payload: PhoneVerifyOTP,
        request: Request,
    ) -> PhoneAuthResponse:
        phone_number = await self.verify_code(
            payload.phone_number, payload.code, PhoneVerificationPurpose.login
        )
        user = await self.get_user_by_phone(phone_number)
        if user is None:
            email = synthetic_phone_email(phone_number)
            user = await self.auth_service.create_random_password_user(
                email, full_name=payload.full_name
            )
            user.phone_number = phone_number
            user.phone_verified_at = utcnow()
            self.session.add(user)
        if not user.is_active:
            raise unauthorized("User is inactive")
        access_token, refresh_token = await self.auth_service.issue_token_pair(user, request)
        await write_audit_log(
            self.session,
            "auth.phone_login",
            user_id=user.id,
            request=request,
            metadata={"phone_number": phone_number},
        )
        return PhoneAuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.jwt_lifetime_seconds,
            user=UserRead.model_validate(user),
        )

    async def link_phone(self, user: User, payload: PhoneLinkRequest, request: Request) -> User:
        phone_number = await self.verify_code(
            payload.phone_number, payload.code, PhoneVerificationPurpose.link
        )
        existing = await self.get_user_by_phone(phone_number)
        if existing is not None and existing.id != user.id:
            raise bad_request("phone_in_use", "Phone number is already linked to another account")
        user.phone_number = phone_number
        user.phone_verified_at = utcnow()
        self.session.add(user)
        await write_audit_log(
            self.session,
            "auth.phone_link",
            user_id=user.id,
            request=request,
            metadata={"phone_number": phone_number},
        )
        return user

    async def unlink_phone(self, user: User, request: Request) -> User:
        if not user.phone_number:
            raise not_found("Phone number is not linked")
        if not user.is_verified:
            raise forbidden("Verify an email address before unlinking your only verified phone")
        old_phone_number = user.phone_number
        user.phone_number = None
        user.phone_verified_at = None
        self.session.add(user)
        await write_audit_log(
            self.session,
            "auth.phone_unlink",
            user_id=user.id,
            request=request,
            metadata={"phone_number": old_phone_number},
        )
        return user

    async def get_user_by_phone(self, phone_number: str) -> User | None:
        result = await self.session.execute(select(User).where(User.phone_number == phone_number))
        return result.scalar_one_or_none()

    @staticmethod
    def _normalize_phone_or_400(phone_number: str) -> str:
        try:
            return normalize_phone_number(phone_number)
        except ValueError as exc:
            raise bad_request("invalid_phone_number", "Invalid phone number") from exc
