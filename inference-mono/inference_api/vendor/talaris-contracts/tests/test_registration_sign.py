"""The self-registration signing contract must be a stable single source of truth:
the same canonical bytes for signer (inference-subnet miner) and verifier
(inference-api), tamper-evident, and replay-bound by nonce/timestamp.
"""
from __future__ import annotations

import hashlib

from nacl.signing import SigningKey

from talaris_contracts import (
    HostedModel,
    SelfRegistration,
    registration_message_for,
    registration_miner_hash,
    registration_signing_message,
    sign_registration_ed25519,
    verify_registration_signature,
)


def _sk() -> SigningKey:
    # Deterministic key so the test never depends on RNG.
    return SigningKey(b"\x11" * 32)


def test_sign_then_verify_roundtrip():
    reg = sign_registration_ed25519(
        signing_key=_sk(),
        peer_id="12D3KooWpeer",
        subnet_node_id=7,
        tee_endpoint="https://miner.example:8000",
        hosted_models=[HostedModel(model_id="m-b"), HostedModel(model_id="m-a")],
        nonce="abc123",
        timestamp=1_700_000_000,
    )
    assert verify_registration_signature(reg, key_type="ed25519") is True
    # hotkey is the pubkey hex; miner_hash binds to the attestation `miner` field.
    assert registration_miner_hash(reg) == hashlib.sha256(
        bytes.fromhex(reg.hotkey)
    ).hexdigest()


def test_canonical_message_is_order_independent_for_models():
    base = dict(
        hotkey="AA" * 32, peer_id="p", subnet_node_id=1,
        tee_endpoint="https://x:8000", nonce="n", timestamp=5,
    )
    a = registration_signing_message(hosted_model_ids=["z", "a", "m"], **base)
    b = registration_signing_message(hosted_model_ids=["a", "m", "z"], **base)
    assert a == b  # sorted models => order-independent
    # hotkey is lowercased in the canonical form
    c = registration_signing_message(hosted_model_ids=["a"], **{**base, "hotkey": "aa" * 32})
    d = registration_signing_message(hosted_model_ids=["a"], **{**base, "hotkey": "AA" * 32})
    assert c == d


def test_tampering_breaks_verification():
    reg = sign_registration_ed25519(
        signing_key=_sk(), peer_id="p", subnet_node_id=1,
        tee_endpoint="https://x:8000", nonce="n", timestamp=5,
    )
    # tamper the endpoint after signing
    tampered = reg.model_copy(update={"tee_endpoint": "https://evil:8000"})
    assert verify_registration_signature(tampered, key_type="ed25519") is False
    # tamper the advertised models
    tampered2 = reg.model_copy(update={"hosted_models": [HostedModel(model_id="x")]})
    assert verify_registration_signature(tampered2, key_type="ed25519") is False


def test_wrong_key_rejected():
    reg = sign_registration_ed25519(
        signing_key=_sk(), peer_id="p", subnet_node_id=1,
        tee_endpoint="https://x:8000",
    )
    other = SelfRegistration(
        **{**reg.model_dump(), "hotkey": bytes(SigningKey(b"\x22" * 32).verify_key).hex()}
    )
    assert verify_registration_signature(other, key_type="ed25519") is False


def test_message_for_matches_builder():
    reg = sign_registration_ed25519(
        signing_key=_sk(), peer_id="p", subnet_node_id=3,
        tee_endpoint="https://x:8000",
        hosted_models=[HostedModel(model_id="m1")], nonce="nn", timestamp=9,
    )
    assert registration_message_for(reg) == registration_signing_message(
        hotkey=reg.hotkey, peer_id="p", subnet_node_id=3,
        tee_endpoint="https://x:8000", nonce="nn", timestamp=9,
        hosted_model_ids=["m1"],
    )
