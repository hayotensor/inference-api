"""Platform-signed miner epoch statistics: canonicalization + sign/verify.

These tests guard the two properties the whole consensus design rests on
(see ``talaris_contracts.miner_stats``): the canonical bytes are a deterministic
function of the DATA (not of dict ordering, ``None``-vs-omitted, or entry order),
and a signature is bound to its epoch/version/kind so nothing can be replayed.
"""

from __future__ import annotations

from nacl.signing import SigningKey

from talaris_contracts import (
    MINER_STATS_KIND,
    AllowlistEntry,
    MinerEpochStatsArtifact,
    MinerStatsEntry,
    canonical_miner_stats_message,
    sign_miner_stats,
    sign_model_allowlist,
    verify_miner_stats,
    verify_model_allowlist,
)


def _entries():
    return [
        {
            "hotkey": "0xAABB",
            "subnet_node_id": 7,
            "model_id": "qwen",
            "model_version": "v1",
            "requests_ok": 10,
            "requests_failed": 2,
            "verified_prompt_tokens": 100,
            "verified_completion_tokens": 50,
            "verified_total_tokens": 150,
            "latency_p95_ms": 900,
            "usage_count_end": 12,
            "usage_chain_head_end": "FEED",
        },
        MinerStatsEntry(hotkey="0xccdd", model_id="glm", verified_total_tokens=5),
    ]


def test_sign_verify_round_trip():
    sk = SigningKey.generate()
    vk = sk.verify_key.encode().hex()
    art = sign_miner_stats(41, _entries(), version=100, signing_key=sk, not_before=100)
    assert verify_miner_stats(art, vk) is True
    assert art.epoch == 41 and art.version == 100 and art.not_before == 100
    assert art.signer_pubkey == vk
    assert all(isinstance(e, MinerStatsEntry) for e in art.entries)


def test_verify_rejects_wrong_key_and_tamper():
    sk = SigningKey.generate()
    vk = sk.verify_key.encode().hex()
    art = sign_miner_stats(41, _entries(), version=100, signing_key=sk)

    assert verify_miner_stats(art, SigningKey.generate().verify_key.encode().hex()) is False
    # A malformed key is a False, never an exception.
    assert verify_miner_stats(art, "not-hex") is False

    # Every signed field is bound: changing any of them invalidates the signature.
    for field, value in (
        ("epoch", 42),
        ("version", 101),
        ("not_before", 1),
    ):
        tampered = art.model_copy(update={field: value})
        assert verify_miner_stats(tampered, vk) is False

    # Inflating a token count is the attack this exists to stop.
    inflated = art.model_copy(
        update={
            "entries": [
                art.entries[0].model_copy(update={"verified_total_tokens": 10**9}),
                *art.entries[1:],
            ]
        }
    )
    assert verify_miner_stats(inflated, vk) is False


def test_canonical_message_is_order_and_none_insensitive():
    entries = _entries()
    assert canonical_miner_stats_message(1, entries, 2) == canonical_miner_stats_message(
        1, list(reversed(entries)), 2
    )

    # An explicit None and an omitted optional field must sign identically, so an
    # ORM row with NULLs and a hand-built entry produce the same bytes.
    explicit = MinerStatsEntry(
        hotkey="0xaa",
        model_id="m",
        model_version=None,
        subnet_node_id=None,
        latency_p95_ms=None,
        usage_count_end=None,
        usage_chain_head_end=None,
    )
    omitted = MinerStatsEntry(hotkey="0xaa", model_id="m")
    assert canonical_miner_stats_message(1, [explicit], 2) == canonical_miner_stats_message(
        1, [omitted], 2
    )

    # Hotkey/chain-head case is not semantically meaningful.
    assert canonical_miner_stats_message(
        1, [MinerStatsEntry(hotkey="0xAB", model_id="m", usage_chain_head_end="FF")], 2
    ) == canonical_miner_stats_message(
        1, [MinerStatsEntry(hotkey="0xab", model_id="m", usage_chain_head_end="ff")], 2
    )

    # Mappings, models, and attribute objects all normalize the same way.
    class _Row:
        hotkey = "0xaa"
        model_id = "m"
        verified_total_tokens = 3

    assert canonical_miner_stats_message(1, [_Row()], 2) == canonical_miner_stats_message(
        1, [{"hotkey": "0xaa", "model_id": "m", "verified_total_tokens": 3}], 2
    )


def test_canonical_message_binds_epoch_kind_and_version():
    entries = _entries()
    assert canonical_miner_stats_message(1, entries, 2) != canonical_miner_stats_message(
        2, entries, 2
    )
    assert canonical_miner_stats_message(1, entries, 2) != canonical_miner_stats_message(
        1, entries, 3
    )
    assert canonical_miner_stats_message(1, entries, 2) != canonical_miner_stats_message(
        1, entries, 2, not_before=5
    )
    assert MINER_STATS_KIND.encode() in canonical_miner_stats_message(1, entries, 2)


def test_kind_domain_separates_stats_from_allowlist():
    """An allowlist signature must never verify as a stats artifact, or vice versa.

    Both are signed by the SAME platform key, so the only thing keeping them
    non-interchangeable is the ``kind`` discriminator inside the canonical message.
    """
    sk = SigningKey.generate()
    vk = sk.verify_key.encode().hex()

    allowlist = sign_model_allowlist(
        [AllowlistEntry(model_id="qwen", model_hash="aabb")], version=5, signing_key=sk
    )
    stats = sign_miner_stats(5, _entries(), version=5, signing_key=sk)

    # Graft each signature onto the other artifact type: neither may verify.
    assert (
        verify_miner_stats(stats.model_copy(update={"signature": allowlist.signature}), vk)
        is False
    )
    assert (
        verify_model_allowlist(
            allowlist.model_copy(update={"signature": stats.signature}), vk
        )
        is False
    )


def test_artifact_round_trips_through_json():
    """The wire form a validator receives must verify after a JSON round-trip."""
    sk = SigningKey.generate()
    vk = sk.verify_key.encode().hex()
    art = sign_miner_stats(41, _entries(), version=100, signing_key=sk, not_before=99)
    revived = MinerEpochStatsArtifact.model_validate(art.model_dump(mode="json"))
    assert verify_miner_stats(revived, vk) is True
    assert revived.canonical_message() == art.canonical_message()
