"""At-rest encryption for provisioned inference tokens.

Provisioned tokens (the bearer credentials sealed into a miner enclave) are
stored encrypted at rest with a DEDICATED Fernet key
(``provisioner_token_encryption_key``) — deliberately NOT ``secret_pepper``,
which is an HMAC key for token *hashing*. Confidentiality of the stored
plaintext token and integrity-hashing of API/service tokens are separate
concerns with separate keys.

The plaintext (decrypted) token is what the platform forwards to the enclave as
a Bearer credential; it is NEVER logged.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from inference_api.config import settings


class TokenEncryptionError(RuntimeError):
    """Raised when the provisioner token encryption key is missing/invalid."""


@lru_cache
def _fernet() -> Fernet:
    secret = settings.provisioner_token_encryption_key
    if secret is None:
        raise TokenEncryptionError(
            "PROVISIONER_TOKEN_ENCRYPTION_KEY is required to encrypt provisioned tokens"
        )
    key = secret.get_secret_value()
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise TokenEncryptionError(
            "PROVISIONER_TOKEN_ENCRYPTION_KEY must be a valid 32-byte url-safe base64 Fernet key"
        ) from exc


def encrypt_token(plaintext: str) -> bytes:
    """Encrypt a token for at-rest storage. Returns Fernet ciphertext bytes."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_token(ciphertext: bytes) -> str:
    """Decrypt a stored token. Raises TokenEncryptionError on tamper/wrong key."""
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise TokenEncryptionError("could not decrypt provisioned token") from exc


def generate_token_encryption_key() -> str:
    """Generate a fresh Fernet key (url-safe base64). For bootstrap/tests."""
    return Fernet.generate_key().decode("utf-8")
