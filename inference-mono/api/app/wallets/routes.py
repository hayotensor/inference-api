from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit_log
from app.auth.dependencies import current_active_user
from app.core.rate_limit import limiter
from app.db.models.user import User
from app.db.session import get_async_session
from app.wallets.schemas import (
    BalanceSyncResponse,
    EVMNonceRequest,
    EVMNonceResponse,
    EVMVerifyRequest,
    EVMWalletRead,
)
from app.wallets.service import WalletService

router = APIRouter(prefix="/wallets/evm", tags=["wallets"])


@router.post("/nonce", response_model=EVMNonceResponse)
@limiter.limit("10/minute")
async def create_evm_nonce(
    request: Request,
    payload: EVMNonceRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> EVMNonceResponse:
    address, nonce, message, expires_at = await WalletService(session).create_nonce(user, payload.address)
    await write_audit_log(
        session,
        "wallet.evm_nonce",
        user_id=user.id,
        request=request,
        metadata={"address": address},
    )
    await session.commit()
    return EVMNonceResponse(address=address, nonce=nonce, message=message, expires_at=expires_at)


@router.post("/verify", response_model=EVMWalletRead)
@limiter.limit("10/minute")
async def verify_evm_wallet(
    request: Request,
    payload: EVMVerifyRequest,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> EVMWalletRead:
    wallet = await WalletService(session).verify_wallet(
        user, payload.address, payload.message, payload.signature
    )
    await write_audit_log(
        session,
        "wallet.evm_verify",
        user_id=user.id,
        request=request,
        metadata={"address": wallet.address},
    )
    await session.commit()
    return EVMWalletRead.model_validate(wallet)


@router.get("", response_model=EVMWalletRead | None)
@limiter.limit("60/minute")
async def get_evm_wallet(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> EVMWalletRead | None:
    wallet = await WalletService(session).get_wallet(user)
    return EVMWalletRead.model_validate(wallet) if wallet else None


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def unlink_evm_wallet(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> Response:
    await WalletService(session).unlink_wallet(user)
    await write_audit_log(session, "wallet.evm_unlink", user_id=user.id, request=request)
    await session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/sync-balances", response_model=BalanceSyncResponse)
@limiter.limit("5/minute")
async def sync_evm_balances(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BalanceSyncResponse:
    wallet, snapshots = await WalletService(session).sync_balances(user)
    await write_audit_log(
        session,
        "wallet.evm_sync_balances",
        user_id=user.id,
        request=request,
        metadata={"address": wallet.address, "snapshots": len(snapshots)},
    )
    await session.commit()
    return BalanceSyncResponse(
        wallet=EVMWalletRead.model_validate(wallet),
        snapshots=[snapshot for snapshot in snapshots],
    )
