from __future__ import annotations

import re

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def normalize_evm_address(address: str) -> str:
    candidate = address.strip()
    if not ADDRESS_RE.match(candidate):
        raise ValueError("Invalid EVM wallet address")
    try:
        from web3 import Web3

        return Web3.to_checksum_address(candidate)
    except Exception:
        return "0x" + candidate[2:].lower()


def addresses_equal(left: str, right: str) -> bool:
    return left.lower() == right.lower()
