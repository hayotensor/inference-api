"""Miner identity mapping: platform registration key <-> libp2p peer ID <-> chain node.

A Talaris miner has three identifiers that must line up:

* the **platform identity** (``SelfRegistration.hotkey``): the 32-byte ed25519 public
  key that signs the self-registration and that the enclave is bound to (``/bind``);
* the **libp2p peer ID**: the subnet node signs with that same ed25519 key, so its peer
  ID is a pure function of the public key (identity multihash of the protobuf-encoded
  key, base58btc - the ``12D3KooW...`` form);
* the **chain node**: Hypertensor records each subnet node's ``peer_info.peer_id`` (and
  indexes it in ``Network.PeerIdSubnetNodeId``). Chain hotkeys are 20-byte EVM accounts
  and are NOT the platform identity.

So the only join that works without a new on-chain field is
``ed25519_peer_id(platform_hotkey) == chain_node.peer_info.peer_id``. Both the platform
(chain-class gate) and the validators (statistics -> chain node join) use this single
helper so they can never derive different peer IDs.

Pure python, no libp2p dependency (the encoding is fixed by the libp2p peer-id spec).
"""

from __future__ import annotations

__all__ = ["ed25519_peer_id", "ed25519_pubkey_from_peer_id"]

# protobuf PublicKey { KeyType Type = 1 (Ed25519 = 1); bytes Data = 2 (32 bytes) }
_PB_ED25519_PREFIX = bytes([0x08, 0x01, 0x12, 0x20])
# multihash: identity (0x00), length 36 (4-byte protobuf header + 32-byte key)
_IDENTITY_MULTIHASH_PREFIX = bytes([0x00, 0x24])
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = []
    while n:
        n, rem = divmod(n, 58)
        out.append(_B58_ALPHABET[rem])
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + "".join(reversed(out))


def _b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        idx = _B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f"invalid base58 character {ch!r}")
        n = n * 58 + idx
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


def _pubkey_bytes(pubkey: str | bytes) -> bytes:
    if isinstance(pubkey, (bytes, bytearray)):
        raw = bytes(pubkey)
    else:
        text = str(pubkey).strip()
        if text[:2].lower() == "0x":
            text = text[2:]
        raw = bytes.fromhex(text)
    if len(raw) != 32:
        raise ValueError(f"ed25519 public key must be 32 bytes, got {len(raw)}")
    return raw


def ed25519_peer_id(pubkey: str | bytes) -> str:
    """The libp2p peer ID (base58btc, ``12D3KooW...``) for an ed25519 public key.

    ``pubkey`` is the raw 32-byte key or its hex (optionally ``0x``-prefixed). Raises
    ``ValueError`` for anything that is not a 32-byte key.
    """
    raw = _pubkey_bytes(pubkey)
    return _b58encode(_IDENTITY_MULTIHASH_PREFIX + _PB_ED25519_PREFIX + raw)


def ed25519_pubkey_from_peer_id(peer_id: str) -> bytes | None:
    """Inverse of :func:`ed25519_peer_id`; ``None`` if ``peer_id`` is not an ed25519
    identity-multihash peer ID (e.g. an RSA/secp256k1 ``Qm...`` ID, or garbage)."""
    try:
        decoded = _b58decode(str(peer_id))
    except ValueError:
        return None
    prefix = _IDENTITY_MULTIHASH_PREFIX + _PB_ED25519_PREFIX
    if len(decoded) != len(prefix) + 32 or not decoded.startswith(prefix):
        return None
    return decoded[len(prefix):]
