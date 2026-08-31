from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from fastapi import Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.schemas import TokenResponse
from app.auth.service import AuthService
from app.core.config import settings
from app.core.errors import bad_request, unauthorized
from app.core.security import expires_in, keyed_hash, synthetic_wallet_email, utcnow
from app.db.models.evm_wallet import EVMWallet
from app.db.models.user import User
from app.db.models.wallet_nonce import WalletNonce
from app.users.schemas import UserRead
from app.wallets.evm import addresses_equal, normalize_evm_address
from app.wallets.signatures import recover_evm_message_address


def _siwe_message(address: str, nonce: str, *, issued_at: str) -> str:
    """Build an EIP-4361 (Sign-In With Ethereum) message.

    The server owns the message text; the client only signs it (personal_sign /
    EIP-191), which ``recover_evm_message_address`` verifies verbatim.
    """
    parsed = urlparse(str(settings.app_url))
    domain = parsed.netloc or "localhost"
    uri = str(settings.app_url).rstrip("/")
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        "Sign in to Talaris.\n\n"
        f"URI: {uri}\n"
        "Version: 1\n"
        f"Chain ID: {settings.wallet_siwe_chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at}"
    )


class WalletNonceLoginRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42)

    @field_validator("address")
    @classmethod
    def address_is_valid(cls, value: str) -> str:
        return normalize_evm_address(value)


class WalletNonceLoginResponse(BaseModel):
    address: str
    message: str
    expires_at: datetime


class WalletVerifyLoginRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42)
    message: str = Field(min_length=1, max_length=2000)
    signature: str = Field(min_length=1, max_length=512)

    @field_validator("address")
    @classmethod
    def address_is_valid(cls, value: str) -> str:
        return normalize_evm_address(value)


class WalletAuthResponse(TokenResponse):
    auth_method: Literal["wallet"] = "wallet"


class WalletAuthService:
    """Unauthenticated wallet login/registration.

    Mirrors :class:`app.auth.phone.PhoneService`: proves wallet ownership, then
    finds-or-creates the user and issues the same JWT access+refresh pair as
    email/password login. Distinct from ``app.wallets`` (association of a wallet
    to an already-authenticated account).
    """

    def __init__(self, session: AsyncSession, auth_service: AuthService) -> None:
        self.session = session
        self.auth_service = auth_service

    async def create_login_nonce(self, address: str) -> WalletNonceLoginResponse:
        normalized = normalize_evm_address(address)
        nonce = secrets.token_urlsafe(32)
        issued_at = utcnow().isoformat()
        message = _siwe_message(normalized, nonce, issued_at=issued_at)
        expires_at = expires_in(seconds=settings.wallet_nonce_ttl_seconds)
        self.session.add(
            WalletNonce(
                user_id=None,
                address=normalized,
                nonce_hash=keyed_hash(message),
                expires_at=expires_at,
            )
        )
        return WalletNonceLoginResponse(address=normalized, message=message, expires_at=expires_at)

    async def login_or_create(
        self, payload: WalletVerifyLoginRequest, request: Request
    ) -> WalletAuthResponse:
        normalized = normalize_evm_address(payload.address)
        nonce_hash = keyed_hash(payload.message)
        result = await self.session.execute(
            select(WalletNonce)
            .where(
                WalletNonce.address == normalized,
                WalletNonce.nonce_hash == nonce_hash,
                WalletNonce.used_at.is_(None),
                WalletNonce.expires_at > utcnow(),
            )
            .order_by(WalletNonce.created_at.desc())
        )
        nonce = result.scalar_one_or_none()
        if nonce is None:
            raise bad_request("invalid_wallet_nonce", "Wallet nonce is invalid or expired")

        recovered = recover_evm_message_address(payload.message, payload.signature)
        if not addresses_equal(recovered, normalized):
            raise bad_request("invalid_wallet_signature", "Wallet signature does not match address")

        now = utcnow()
        wallet = (
            await self.session.execute(select(EVMWallet).where(EVMWallet.address == normalized))
        ).scalar_one_or_none()
        if wallet is not None:
            user = await self.session.get(User, wallet.user_id)
            if user is None:
                raise bad_request("invalid_wallet_nonce", "Wallet nonce is invalid or expired")
        else:
            user = await self.auth_service.create_random_password_user(
                synthetic_wallet_email(normalized)
            )
            self.session.add(EVMWallet(user_id=user.id, address=normalized, verified_at=now))

        if not user.is_active:
            raise unauthorized("User is inactive")

        if user.wallet_verified_at is None:
            user.wallet_verified_at = now
            self.session.add(user)
        nonce.used_at = now
        self.session.add(nonce)

        access_token, refresh_token = await self.auth_service.issue_token_pair(user, request)
        await write_audit_log(
            self.session,
            "auth.wallet_login",
            user_id=user.id,
            request=request,
            metadata={"address": normalized},
        )
        return WalletAuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.jwt_lifetime_seconds,
            user=UserRead.model_validate(user),
        )
