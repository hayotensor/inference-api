from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.wallets.evm import normalize_evm_address


class EVMNonceRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42)

    @field_validator("address")
    @classmethod
    def address_is_valid(cls, value: str) -> str:
        return normalize_evm_address(value)


class EVMNonceResponse(BaseModel):
    address: str
    nonce: str
    message: str
    expires_at: datetime


class EVMVerifyRequest(BaseModel):
    address: str = Field(min_length=42, max_length=42)
    message: str = Field(min_length=1, max_length=2000)
    signature: str = Field(min_length=1, max_length=512)

    @field_validator("address")
    @classmethod
    def address_is_valid(cls, value: str) -> str:
        return normalize_evm_address(value)


class EVMWalletRead(BaseModel):
    address: str
    verified_at: datetime
    last_balance_sync_at: datetime | None = None

    model_config = {"from_attributes": True}


class BalanceSyncItem(BaseModel):
    chain: str
    token_type: str
    token_contract_address: str | None = None
    raw_balance: str
    normalized_balance: Decimal
    inference_token_allowance: int
    block_number: int | None = None
    error_message: str | None = None
    checked_at: datetime

    model_config = {"from_attributes": True}


class BalanceSyncResponse(BaseModel):
    wallet: EVMWalletRead
    snapshots: list[BalanceSyncItem]
