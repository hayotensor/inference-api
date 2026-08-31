"""Provision wire format.

Authoritative sources:
- ``tee_wrapper.security.credentials.Credentials`` = {admin_token, inference_keys}.
- ``tee_wrapper.routers.provision.ProvisionRequest`` = {validator_pubkey, sealed, signature}.

The enclave does ``json.loads(unseal(bytes.fromhex(sealed)))`` and expects
``admin_token`` (required) plus optional ``inference_keys`` (key_id -> token).
The signature is an Ed25519 signature by the validator over the raw sealed bytes.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    """Credentials sealed to the enclave during provisioning.

    Mirrors ``tee_wrapper.security.credentials.Credentials``. Serialized to JSON,
    sealed to the enclave's X25519 ``sealing_key`` via a libsodium SealedBox, then
    unsealed and ``json.loads``-ed by the enclave, which reads ``admin_token`` and
    ``inference_keys``.
    """

    model_config = ConfigDict(extra="allow")

    admin_token: str
    inference_keys: dict[str, str] = Field(default_factory=dict)  # key_id -> token


class ProvisionRequest(BaseModel):
    """POST /provision request body.

    Mirrors ``tee_wrapper.routers.provision.ProvisionRequest``:
    - ``validator_pubkey``: hex Ed25519 verify key of the provisioning validator.
    - ``sealed``: hex libsodium sealed-box ciphertext (creds JSON sealed to the
      enclave's X25519 ``sealing_key``).
    - ``signature``: hex Ed25519 signature by the validator over the raw sealed bytes.
    """

    model_config = ConfigDict(extra="allow")

    validator_pubkey: str
    sealed: str
    signature: str


__all__ = ["Credentials", "ProvisionRequest"]
