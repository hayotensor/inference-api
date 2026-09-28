import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.users.schemas import UserCreatePublic, UserRead


class EmailPasswordLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RegisterRequest(UserCreatePublic):
    """POST /auth/register body. Extends the public user-create schema with an
    optional HYP-65 referral code (the `ref` query param on a shared
    `{app_url}/signup?ref=<code>` link, threaded through by the frontend).

    Deliberately NOT added to `UserCreatePublic` itself: that schema is
    passed straight into `UserManager.create()`, which turns it into
    `User(**user_dict)` via `create_update_dict()` -- and `User` has no
    `referral_code_used` column, so a stray extra field there would crash
    every registration that included it. The /auth/register handler builds a
    bare `UserCreatePublic` (not `payload` itself) for that call and handles
    `referral_code` separately via `ReferralService.attribute_signup`.
    """

    referral_code: str | None = Field(default=None, max_length=32)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserRead


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class RequestVerifyEmail(BaseModel):
    email: EmailStr


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class MessageResponse(BaseModel):
    detail: str


class SSOTokenResponse(BaseModel):
    sso_token: str
    expires_in: int


class OAuthLoginResult(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user_id: uuid.UUID
    email: EmailStr
    is_verified: bool
    created_at: datetime
