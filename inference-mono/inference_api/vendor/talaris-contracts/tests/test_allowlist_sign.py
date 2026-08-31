"""Platform-signed model allowlist: canonicalization + sign/verify round-trip."""

from __future__ import annotations

from nacl.signing import SigningKey

from talaris_contracts import (
    AllowlistEntry,
    ModelAllowlistArtifact,
    canonical_allowlist_message,
    sign_model_allowlist,
    verify_model_allowlist,
)


def _entries():
    return [
        {"model_id": "qwen", "model_version": "v1", "model_hash": "AABB", "label": "Qwen"},
        AllowlistEntry(model_id="glm", model_hash="ccdd"),
    ]


def test_sign_verify_round_trip():
    sk = SigningKey.generate()
    vk = sk.verify_key.encode().hex()
    art = sign_model_allowlist(_entries(), version=3, signing_key=sk, not_before=100)
    assert verify_model_allowlist(art, vk) is True
    assert art.signer_pubkey == vk
    assert art.version == 3 and art.not_before == 100
    # entries coerced to AllowlistEntry
    assert all(isinstance(e, AllowlistEntry) for e in art.entries)


def test_verify_rejects_wrong_key_and_tamper():
    sk = SigningKey.generate()
    vk = sk.verify_key.encode().hex()
    art = sign_model_allowlist(_entries(), version=3, signing_key=sk)
    # wrong key
    assert verify_model_allowlist(art, SigningKey.generate().verify_key.encode().hex()) is False
    # tampering the version invalidates the signature
    art.version = 4
    assert verify_model_allowlist(art, vk) is False
    # malformed key never raises
    assert verify_model_allowlist(art, "not-hex") is False


def test_canonical_message_is_order_independent_and_normalized():
    a, b = _entries()
    m1 = canonical_allowlist_message([a, b], 3, 100)
    m2 = canonical_allowlist_message([b, a], 3, 100)
    assert m1 == m2  # sorted by (model_id, model_version, model_hash)
    # model_hash lowercased; the domain discriminator is bound
    assert b"aabb" in m1 and b"AABB" not in m1
    assert b"model-allowlist/v1" in m1
    # version is bound so a re-versioned snapshot has different bytes
    assert canonical_allowlist_message([a, b], 4, 100) != m1


def test_artifact_canonical_message_matches_helper():
    art = ModelAllowlistArtifact(
        entries=[AllowlistEntry(model_id="m", model_hash="aa")],
        version=1,
        signature="00",
    )
    assert art.canonical_message() == canonical_allowlist_message(art.entries, 1, None)
