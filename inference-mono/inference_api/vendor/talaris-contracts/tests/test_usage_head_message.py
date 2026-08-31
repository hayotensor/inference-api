"""Byte-equality test for the enclave usage-head canonical message.

Asserts ``talaris_contracts.usage_head_signing_message`` produces bytes IDENTICAL
to the producer's ``tee_wrapper.routers.usage._head_signing_message`` (the byte-format
authority for the ``head_signature`` served by ``GET /usage/receipts``) — so
inference-api can verify the enclave's cumulative usage anchor without importing
``tee_wrapper``.

This helper used to live in the subnet's usage-batch DAG publisher, which pulled the
whole merkle-DAG surface in with it. It now lives in the shared contracts package,
consumed by the platform's receipts collector; this test is what keeps the two
copies from drifting.

Loading the producer mirrors ``test_receipts_auth_message``: prefer the importable
``tee_wrapper``, and fall back to loading the canonical file directly (the router
module imports FastAPI, so a direct load is the more robust path here).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

from talaris_contracts import USAGE_HEAD_DOMAIN, usage_head_signing_message


_PRODUCER_USAGE_CANDIDATES = (
    Path("/home/rizzo/talaris-inference/inference-subnet-miner/src/tee_wrapper/routers/usage.py"),
    Path(__file__).resolve().parents[3] / "inference-subnet-miner/src/tee_wrapper/routers/usage.py",
)


def _load_producer():
    """Return the producer ``_head_signing_message`` (+ domain tag), or skip."""
    try:
        from tee_wrapper.routers.usage import (  # type: ignore
            _USAGE_HEAD_DOMAIN as tag,
            _head_signing_message as fn,
        )
        return fn, tag
    except Exception:  # noqa: BLE001 - absent/partial checkout; fall back to file load
        pass

    for path in _PRODUCER_USAGE_CANDIDATES:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_producer_tee_usage", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:  # noqa: BLE001 - e.g. FastAPI/relative-import unavailable
            sys.modules.pop(spec.name, None)
            continue
        fn = getattr(module, "_head_signing_message", None)
        tag = getattr(module, "_USAGE_HEAD_DOMAIN", None)
        if fn is not None and tag is not None:
            return fn, tag

    pytest.skip("producer tee_wrapper.routers.usage._head_signing_message unavailable")


CASES = [
    (0, "", None),           # genesis: no rows yet, no chain head
    (1, "abc", None),        # paged by since_row_id only (the collector's default)
    (2, "h2", None),
    (7, "abc", 5.0),         # float epoch -> repr() -> "5.0"
    (7, "abc", 5),           # int epoch -> "5" (must differ from the float case)
    (12345, "f" * 64, 0.0),
]


def test_domain_tag_byte_identical():
    _, producer_tag = _load_producer()
    assert USAGE_HEAD_DOMAIN == producer_tag
    assert USAGE_HEAD_DOMAIN == b"tee-wrapper/usage-head/v1"


@pytest.mark.parametrize("usage_count,head,epoch", CASES)
def test_usage_head_signing_message_byte_identical(usage_count, head, epoch):
    producer, _ = _load_producer()
    mine = usage_head_signing_message(usage_count, head, epoch)
    assert mine == producer(usage_count, head, epoch)

    # Spell out the wire format so a divergence is obvious at the byte level.
    expected = bytearray()
    for field in (
        USAGE_HEAD_DOMAIN,
        str(usage_count).encode(),
        head.encode(),
        (b"" if epoch is None else repr(epoch).encode()),
    ):
        expected += len(field).to_bytes(4, "big")
        expected += field
    assert mine == bytes(expected)


def test_length_prefixing_prevents_field_collision():
    """The length prefixes are what stop (count, head) pairs from colliding."""
    assert usage_head_signing_message(1, "23", None) != usage_head_signing_message(12, "3", None)


def test_int_and_float_epoch_are_distinct():
    """The producer encodes epoch via ``repr``, so 5 and 5.0 are different messages."""
    assert usage_head_signing_message(7, "abc", 5) != usage_head_signing_message(7, "abc", 5.0)


def test_none_head_normalizes_to_empty_string():
    """A missing chain head must sign the same as an empty one (producer behavior)."""
    assert usage_head_signing_message(0, None, None) == usage_head_signing_message(0, "", None)
