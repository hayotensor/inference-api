"""Byte-equality test for the bound-miner receipts-auth canonical message.

Asserts ``talaris_contracts.receipts_auth_message`` produces bytes IDENTICAL to the
producer's ``tee_wrapper.security.miner.receipts_auth_message`` (the byte-format
authority for the ``X-Miner-Signature`` proof, decision #9) for several
``(timestamp, path)`` cases — so the subnet collector can sign the proof without
importing ``tee_wrapper``.

The producer is imported normally when an up-to-date ``tee_wrapper`` is on the path;
if the editable-installed checkout predates ``receipts_auth_message`` we load the
canonical producer file directly so the byte-equality assertion still runs against
the real source.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from talaris_contracts import RECEIPTS_AUTH_DOMAIN_TAG, receipts_auth_message


# Canonical producer source (the file the wire format is defined in, decision #9).
_PRODUCER_MINER_CANDIDATES = (
    Path("/home/rizzo/talaris-inference/inference-subnet-miner/src/tee_wrapper/security/miner.py"),
    Path(__file__).resolve().parents[3]
    / "inference-subnet-miner/src/tee_wrapper/security/miner.py",
)


def _load_producer():
    """Return the producer ``receipts_auth_message`` (+ tag), or skip if unavailable.

    Prefer the importable ``tee_wrapper``; fall back to loading the canonical producer
    file directly when the editable-installed checkout predates the function.
    """
    try:
        from tee_wrapper.security.miner import (  # type: ignore
            RECEIPTS_AUTH_DOMAIN_TAG as tag,
            receipts_auth_message as fn,
        )
        return fn, tag
    except Exception:  # noqa: BLE001 - older/absent checkout; fall back to file load
        pass

    for path in _PRODUCER_MINER_CANDIDATES:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_producer_tee_miner", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        # Register before exec: the producer file defines a @dataclass whose
        # (string-annotation) resolution looks the module up in sys.modules.
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001
            sys.modules.pop(spec.name, None)
            continue
        fn = getattr(module, "receipts_auth_message", None)
        tag = getattr(module, "RECEIPTS_AUTH_DOMAIN_TAG", None)
        if fn is not None and tag is not None:
            return fn, tag

    pytest.skip("producer tee_wrapper.security.miner.receipts_auth_message unavailable")


CASES = [
    (0, "/usage/receipts"),
    (1, "/usage/receipts"),
    (1_700_000_000, "/usage/receipts"),
    (2**63 - 1, "/usage/receipts"),  # near-max u64 boundary
    (1_700_000_000, "/usage/receipts?since_row_id=42"),  # path-with-query case
    (42, "/other/endpoint"),  # path binding distinguishes endpoints
]


def test_domain_tag_byte_identical():
    fn, producer_tag = _load_producer()
    assert RECEIPTS_AUTH_DOMAIN_TAG == producer_tag
    assert RECEIPTS_AUTH_DOMAIN_TAG == b"tee-wrapper/receipts-auth/v1"


@pytest.mark.parametrize("timestamp,path", CASES)
def test_receipts_auth_message_byte_identical(timestamp, path):
    producer, _ = _load_producer()
    mine = receipts_auth_message(timestamp, path)
    theirs = producer(timestamp, path)
    assert mine == theirs
    # Spell out the exact wire format so a divergence is obvious at the byte level.
    assert mine == (
        b"tee-wrapper/receipts-auth/v1"
        + int(timestamp).to_bytes(8, "big")
        + path.encode()
    )


def test_negative_timestamp_rejected():
    fn, _ = _load_producer()
    with pytest.raises(ValueError):
        receipts_auth_message(-1, "/usage/receipts")
    with pytest.raises(ValueError):
        fn(-1, "/usage/receipts")
