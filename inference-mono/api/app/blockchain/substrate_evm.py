from __future__ import annotations

from app.blockchain.erc20 import BalanceRead, BlockchainConfigError, BlockchainReadError, build_balance_read
from app.core.config import settings


class SubstrateEVMNativeClient:
    def __init__(self, *, rpc_url: str | None = None, decimals: int | None = None) -> None:
        self.rpc_url = rpc_url or settings.substrate_evm_rpc_url
        self.decimals = settings.substrate_native_decimals if decimals is None else decimals

    def get_balance(self, address: str) -> BalanceRead:
        if not self.rpc_url:
            raise BlockchainConfigError("Substrate EVM RPC URL is required")
        try:
            from web3 import Web3

            web3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 10}))
            checksum_address = Web3.to_checksum_address(address)
            raw_balance = int(web3.eth.get_balance(checksum_address))
            return build_balance_read(raw_balance, self.decimals, int(web3.eth.block_number))
        except BlockchainConfigError:
            raise
        except Exception as exc:
            raise BlockchainReadError(str(exc)) from exc
