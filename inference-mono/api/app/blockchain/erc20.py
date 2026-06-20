from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR


ERC20_BALANCE_OF_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    }
]


class BlockchainConfigError(RuntimeError):
    pass


class BlockchainReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class BalanceRead:
    raw_balance: int
    normalized_balance: Decimal
    inference_token_allowance: int
    block_number: int | None


def normalize_token_amount(raw_balance: int, decimals: int) -> Decimal:
    return Decimal(raw_balance) / (Decimal(10) ** decimals)


def inference_allowance_from_normalized(normalized_balance: Decimal) -> int:
    return int(normalized_balance.to_integral_value(rounding=ROUND_FLOOR))


def build_balance_read(raw_balance: int, decimals: int, block_number: int | None) -> BalanceRead:
    normalized = normalize_token_amount(raw_balance, decimals)
    return BalanceRead(
        raw_balance=raw_balance,
        normalized_balance=normalized,
        inference_token_allowance=inference_allowance_from_normalized(normalized),
        block_number=block_number,
    )
