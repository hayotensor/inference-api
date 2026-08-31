"""Reproduce tee_wrapper/tests/test_provision.py's seal+sign flow.

Asserts:
- build_provision_request produces fields equivalent to the manual SealedBox +
  Ed25519 sign path the producer tests use.
- An enclave (holding the X25519 private key) can json.loads(SealedBox(priv)
  .decrypt(sealed)) back to exactly the original creds.
- A live TEE app accepts the request from build_provision_request (end-to-end).
"""

from __future__ import annotations

import json
import secrets

import pytest
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey

from talaris_contracts import (
    Credentials,
    ProvisionRequest,
    build_provision_request,
    seal_credentials,
)


def test_seal_credentials_unseals_back_to_creds():
    enclave_priv = PrivateKey.generate()
    sealing_pub_hex = bytes(enclave_priv.public_key).hex()

    creds = Credentials(admin_token="A", inference_keys={"k1": "I"})
    sealed = seal_credentials(creds, sealing_pub_hex)

    # The enclave path: decrypt with the private key, json.loads, read fields.
    plaintext = SealedBox(enclave_priv).decrypt(sealed)
    recovered = json.loads(plaintext)
    assert recovered["admin_token"] == "A"
    assert recovered["inference_keys"] == {"k1": "I"}


def test_seal_accepts_plain_dict_creds():
    enclave_priv = PrivateKey.generate()
    sealing_pub_hex = bytes(enclave_priv.public_key).hex()
    sealed = seal_credentials(
        {"admin_token": "A", "inference_keys": {}}, sealing_pub_hex
    )
    recovered = json.loads(SealedBox(enclave_priv).decrypt(sealed))
    assert recovered == {"admin_token": "A", "inference_keys": {}}


def test_build_provision_request_matches_manual_path():
    enclave_priv = PrivateKey.generate()
    sealing_pub_hex = bytes(enclave_priv.public_key).hex()
    validator_sk = SigningKey.generate()

    creds = Credentials(admin_token="A", inference_keys={"k1": "I"})
    req = build_provision_request(creds, sealing_pub_hex, validator_sk)

    assert isinstance(req, ProvisionRequest)
    # validator_pubkey is the signer's verify key (matches manual path).
    assert req.validator_pubkey == bytes(validator_sk.verify_key).hex()

    sealed_bytes = bytes.fromhex(req.sealed)

    # The signature must verify over the RAW sealed bytes (the producer signs
    # `sealed`, i.e. validator_sk.sign(sealed).signature.hex()).
    VerifyKey(bytes.fromhex(req.validator_pubkey)).verify(
        sealed_bytes, bytes.fromhex(req.signature)
    )

    # And the sealed ciphertext unseals back to the original creds (the manual
    # SealedBox(PublicKey(...)).encrypt(json.dumps(creds).encode()) path is
    # ciphertext-different per call, but the recovered plaintext is identical).
    recovered = json.loads(SealedBox(enclave_priv).decrypt(sealed_bytes))
    assert recovered == {"admin_token": "A", "inference_keys": {"k1": "I"}}


def test_manual_producer_path_is_reproducible_by_helper():
    """The exact manual flow from tee_wrapper tests, verified against the helper."""
    enclave_priv = PrivateKey.generate()
    sealing_pub_hex = bytes(enclave_priv.public_key).hex()
    validator_sk = SigningKey.generate()
    creds = {"admin_token": "A", "inference_keys": {"k1": "I"}}

    # Manual producer path (verbatim from tee_wrapper/tests/test_provision.py):
    box = SealedBox(PublicKey(bytes.fromhex(sealing_pub_hex)))
    manual_sealed = box.encrypt(json.dumps(creds).encode())
    manual_sig = validator_sk.sign(manual_sealed).signature.hex()

    # Helper path:
    req = build_provision_request(creds, sealing_pub_hex, validator_sk)

    # Same signer pubkey; both signatures verify over their respective sealed
    # bytes; both decrypt to the same creds. (Ciphertext differs per ephemeral
    # SealedBox key, which is expected and not a wire invariant.)
    assert req.validator_pubkey == bytes(validator_sk.verify_key).hex()
    VerifyKey(bytes.fromhex(req.validator_pubkey)).verify(
        manual_sealed, bytes.fromhex(manual_sig)
    )
    VerifyKey(bytes.fromhex(req.validator_pubkey)).verify(
        bytes.fromhex(req.sealed), bytes.fromhex(req.signature)
    )
    assert json.loads(SealedBox(enclave_priv).decrypt(manual_sealed)) == creds
    assert (
        json.loads(SealedBox(enclave_priv).decrypt(bytes.fromhex(req.sealed)))
        == creds
    )


# --- End-to-end against the real TEE app (requires tee_wrapper on PYTHONPATH) --
pytest.importorskip("tee_wrapper")
from fastapi.testclient import TestClient  # noqa: E402

from tee_wrapper.app import create_app  # noqa: E402


def test_build_provision_request_accepted_by_real_app(tee_settings):
    validator = SigningKey.generate()
    vk_hex = bytes(validator.verify_key).hex()
    app = create_app(tee_settings(validator_static_keys=vk_hex))
    with TestClient(app) as c:
        doc = c.get(f"/attestation?nonce={secrets.token_hex(16)}").json()
        sealing_pub = doc["public_bundle"]["sealing_key"]
        req = build_provision_request(
            Credentials(admin_token="A", inference_keys={"k1": "I"}),
            sealing_pub,
            validator,
        )
        resp = c.post("/provision", json=req.model_dump())
        assert resp.status_code == 200, resp.text
        assert resp.json()["provisioned"] is True
