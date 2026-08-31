"""Byte-equality and round-trip tests for the validator-list canonical message.

Asserts:
- talaris_contracts.canonical_list_message produces bytes IDENTICAL to the
  producer's tee_wrapper.security.validators.canonical_list_message for several
  inputs (including not_before set/unset and unsorted/mixed-case inputs).
- sign_validator_list produces a payload that round-trips through the producer's
  ValidatorAuthorizer._verify_and_extract (signature verifies, version accepted).
"""

from __future__ import annotations

import pytest
from nacl.signing import SigningKey

from talaris_contracts import canonical_list_message, sign_validator_list

pytest.importorskip("tee_wrapper")
from tee_wrapper.security.validators import (  # noqa: E402
    ValidatorAuthorizer,
    canonical_list_message as prod_canonical_list_message,
)


def _key_hex() -> str:
    return bytes(SigningKey.generate().verify_key).hex()


CASES = [
    ([], 1, None),
    ([_key_hex()], 1, None),
    ([_key_hex(), _key_hex()], 7, None),
    ([_key_hex(), _key_hex()], 7, 1_700_000_000),
    # unsorted + mixed case to exercise the sort/lowercase normalization
    ([_key_hex().upper(), _key_hex()], 42, 999),
    ([_key_hex(), _key_hex(), _key_hex()], 0, 0),
]


@pytest.mark.parametrize("validators,version,not_before", CASES)
def test_canonical_message_byte_identical(validators, version, not_before):
    mine = canonical_list_message(validators, version, not_before)
    theirs = prod_canonical_list_message(validators, version, not_before)
    assert mine == theirs


def test_sort_keys_reorders_top_level():
    # With not_before present, sort_keys=True orders: not_before, validators, version.
    msg = canonical_list_message(["aa" * 32], 5, 123)
    text = msg.decode()
    assert text.index('"not_before"') < text.index('"validators"') < text.index(
        '"version"'
    )


def test_sign_validator_list_roundtrips_through_authorizer():
    root = SigningKey.generate()
    root_hex = bytes(root.verify_key).hex()
    validators = [_key_hex(), _key_hex()]

    vl = sign_validator_list(validators, version=5, root_key=root, not_before=None)

    authorizer = ValidatorAuthorizer(
        static_keys=[], root_key=root_hex, list_url=None, refresh_s=0.0
    )
    payload = vl.model_dump()
    dynamic, version = authorizer._verify_and_extract(payload, now=1_700_000_000.0)

    assert version == 5
    assert dynamic == {v.lower() for v in validators}


def test_sign_validator_list_with_not_before_roundtrips():
    root = SigningKey.generate()
    root_hex = bytes(root.verify_key).hex()
    validators = [_key_hex()]
    not_before = 1_600_000_000

    vl = sign_validator_list(validators, version=3, root_key=root, not_before=not_before)
    assert vl.not_before == not_before

    authorizer = ValidatorAuthorizer(
        static_keys=[], root_key=root_hex, list_url=None, refresh_s=0.0
    )
    # now must be >= not_before for the list to be valid.
    dynamic, version = authorizer._verify_and_extract(
        vl.model_dump(), now=float(not_before + 10)
    )
    assert version == 3
    assert dynamic == {v.lower() for v in validators}


def test_tampered_signature_rejected_by_authorizer():
    root = SigningKey.generate()
    attacker = SigningKey.generate()
    root_hex = bytes(root.verify_key).hex()
    validators = [_key_hex()]

    # Sign with the attacker key instead of the root key.
    vl = sign_validator_list(validators, version=2, root_key=attacker)

    authorizer = ValidatorAuthorizer(
        static_keys=[], root_key=root_hex, list_url=None, refresh_s=0.0
    )
    with pytest.raises(ValueError):
        authorizer._verify_and_extract(vl.model_dump(), now=1_700_000_000.0)
