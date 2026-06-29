"""Load the platform Ed25519 provisioning SigningKey from config.

The platform signs every POST /provision payload with this key
(``talaris_contracts.build_provision_request`` signs the sealed credential bytes
with it); the enclave accepts the provision only if the corresponding verify key
is one of its ``validator_static_keys``. The key material is sourced from
config: an inline hex seed (``provisioner_signing_key_hex``) takes priority,
otherwise a filesystem path (``provisioner_signing_key_path``) holding a 32-byte
raw seed or a 64-hex seed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from nacl.signing import SigningKey

from inference_api.config import settings


class ProvisionerKeyError(RuntimeError):
    """Raised when the provisioner signing key cannot be loaded."""


def _signing_key_from_seed_bytes(seed: bytes) -> SigningKey:
    if len(seed) == 64:
        # Hex-encoded 32-byte seed stored as text bytes.
        try:
            seed = bytes.fromhex(seed.decode("ascii").strip())
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProvisionerKeyError("provisioner key file is not valid hex") from exc
    if len(seed) != 32:
        raise ProvisionerKeyError(
            f"provisioner signing key must be a 32-byte seed (got {len(seed)} bytes)"
        )
    return SigningKey(seed)


@lru_cache
def load_provisioner_signing_key() -> SigningKey:
    """Load (and cache) the platform provisioning SigningKey."""
    inline = settings.provisioner_signing_key_hex
    if inline is not None:
        raw = inline.get_secret_value().strip()
        try:
            return SigningKey(bytes.fromhex(raw))
        except ValueError as exc:
            raise ProvisionerKeyError(
                "PROVISIONER_SIGNING_KEY_HEX must be a 32-byte (64-hex) Ed25519 seed"
            ) from exc

    path = settings.provisioner_signing_key_path
    if path:
        data = Path(path).read_bytes().strip()
        return _signing_key_from_seed_bytes(data)

    raise ProvisionerKeyError(
        "provisioner signing key not configured: set PROVISIONER_SIGNING_KEY_HEX or "
        "PROVISIONER_SIGNING_KEY_PATH"
    )


def provisioner_verify_key_hex() -> str:
    """The hex verify key of the platform provisioner (for diagnostics / config)."""
    return bytes(load_provisioner_signing_key().verify_key).hex()
