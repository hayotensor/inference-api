from __future__ import annotations

import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.blockchain.erc20 import BalanceRead
from app.blockchain.ethereum import EthereumERC20Client
from app.blockchain.substrate_evm import SubstrateEVMNativeClient
from app.core.config import settings
from app.core.errors import bad_request, not_found
from app.core.security import expires_in, keyed_hash, utcnow
from app.db.models.crypto_balance_snapshot import CryptoBalanceSnapshot
from app.db.models.evm_wallet import EVMWallet
from app.db.models.user import User
from app.db.models.wallet_nonce import WalletNonce
from app.wallets.evm import addresses_equal, normalize_evm_address
from app.wallets.signatures import recover_evm_message_address


def _wallet_message(address: str, nonce: str) -> str:
    return (
        "Sign this message to connect your wallet to Inference API.\n\n"
        f"Address: {address}\n"
        f"Nonce: {nonce}\n"
        "Only sign this message if you initiated this request."
    )


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_nonce(self, user: User, address: str) -> tuple[str, str, str, object]:
        normalized = normalize_evm_address(address)
        nonce = secrets.token_urlsafe(32)
        message = _wallet_message(normalized, nonce)
        expires_at = expires_in(seconds=settings.wallet_nonce_ttl_seconds)
        self.session.add(
            WalletNonce(
                user_id=user.id,
                address=normalized,
                nonce_hash=keyed_hash(message),
                expires_at=expires_at,
            )
        )
        return normalized, nonce, message, expires_at

    async def verify_wallet(self, user: User, address: str, message: str, signature: str) -> EVMWallet:
        normalized = normalize_evm_address(address)
        nonce_hash = keyed_hash(message)
        result = await self.session.execute(
            select(WalletNonce)
            .where(
                WalletNonce.user_id == user.id,
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

        recovered = recover_evm_message_address(message, signature)
        if not addresses_equal(recovered, normalized):
            raise bad_request("invalid_wallet_signature", "Wallet signature does not match address")

        existing_for_address = (
            await self.session.execute(select(EVMWallet).where(EVMWallet.address == normalized))
        ).scalar_one_or_none()
        if existing_for_address is not None and existing_for_address.user_id != user.id:
            raise bad_request("wallet_already_linked", "Wallet address is already linked")

        wallet = (
            await self.session.execute(select(EVMWallet).where(EVMWallet.user_id == user.id))
        ).scalar_one_or_none()
        now = utcnow()
        if wallet is None:
            wallet = EVMWallet(user_id=user.id, address=normalized, verified_at=now)
        else:
            wallet.address = normalized
            wallet.verified_at = now
        nonce.used_at = now
        self.session.add_all([wallet, nonce])
        await self.session.flush()
        return wallet

    async def get_wallet(self, user: User) -> EVMWallet | None:
        result = await self.session.execute(select(EVMWallet).where(EVMWallet.user_id == user.id))
        return result.scalar_one_or_none()

    async def require_wallet(self, user: User) -> EVMWallet:
        wallet = await self.get_wallet(user)
        if wallet is None:
            raise not_found("EVM wallet not connected")
        return wallet

    async def unlink_wallet(self, user: User) -> None:
        wallet = await self.get_wallet(user)
        if wallet is not None:
            await self.session.delete(wallet)

    async def sync_balances(self, user: User) -> tuple[EVMWallet, list[CryptoBalanceSnapshot]]:
        wallet = await self.require_wallet(user)
        snapshots: list[CryptoBalanceSnapshot] = []
        snapshots.append(
            await self._read_and_store(
                user,
                wallet,
                chain="ethereum",
                token_type="erc20",
                token_contract_address=settings.ethereum_erc20_contract_address,
                reader=lambda: EthereumERC20Client().get_balance(wallet.address),
            )
        )
        snapshots.append(
            await self._read_and_store(
                user,
                wallet,
                chain="substrate_evm",
                token_type="native",
                token_contract_address=None,
                reader=lambda: SubstrateEVMNativeClient().get_balance(wallet.address),
            )
        )
        wallet.last_balance_sync_at = utcnow()
        self.session.add(wallet)

        from app.usage.service import UsageService

        await UsageService(self.session).recalculate_current_period(user)
        return wallet, snapshots

    async def _read_and_store(
        self,
        user: User,
        wallet: EVMWallet,
        *,
        chain: str,
        token_type: str,
        token_contract_address: str | None,
        reader,
    ) -> CryptoBalanceSnapshot:
        error_message: str | None = None
        balance: BalanceRead | None = None
        try:
            balance = reader()
        except Exception as exc:
            error_message = str(exc)[:512]

        snapshot = CryptoBalanceSnapshot(
            user_id=user.id,
            wallet_address=wallet.address,
            chain=chain,
            token_type=token_type,
            token_contract_address=token_contract_address,
            raw_balance=str(balance.raw_balance if balance else 0),
            normalized_balance=balance.normalized_balance if balance else Decimal("0"),
            inference_token_allowance=balance.inference_token_allowance if balance else 0,
            block_number=balance.block_number if balance else None,
            error_message=error_message,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot
