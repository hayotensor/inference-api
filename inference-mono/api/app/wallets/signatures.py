from __future__ import annotations

from app.wallets.evm import normalize_evm_address


def recover_evm_message_address(message: str, signature: str) -> str:
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
        return normalize_evm_address(recovered)
    except Exception as exc:
        raise ValueError("Invalid wallet signature") from exc
