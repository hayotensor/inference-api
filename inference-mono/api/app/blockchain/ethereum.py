from __future__ import annotations

from app.blockchain.erc20 import (
    ERC20_BALANCE_OF_ABI,
    BalanceRead,
    BlockchainConfigError,
    BlockchainReadError,
    build_balance_read,
)
from app.core.config import settings


class EthereumERC20Client:
    def __init__(
        self,
        *,
        rpc_url: str | None = None,
        contract_address: str | None = None,
        decimals: int | None = None,
    ) -> None:
        self.rpc_url = rpc_url or settings.ethereum_rpc_url
        self.contract_address = contract_address or settings.ethereum_erc20_contract_address
        self.decimals = settings.ethereum_erc20_decimals if decimals is None else decimals

    def get_balance(self, address: str) -> BalanceRead:
        if not self.rpc_url or not self.contract_address:
            raise BlockchainConfigError("Ethereum RPC URL and ERC20 contract address are required")
        try:
            from web3 import Web3

            web3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 10}))
            checksum_address = Web3.to_checksum_address(address)
            checksum_contract = Web3.to_checksum_address(self.contract_address)
            contract = web3.eth.contract(address=checksum_contract, abi=ERC20_BALANCE_OF_ABI)
            raw_balance = int(contract.functions.balanceOf(checksum_address).call())
            return build_balance_read(raw_balance, self.decimals, int(web3.eth.block_number))
        except BlockchainConfigError:
            raise
        except Exception as exc:
            raise BlockchainReadError(str(exc)) from exc
