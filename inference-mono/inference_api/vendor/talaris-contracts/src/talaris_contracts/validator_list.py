"""Root-signed validator list wire format.

Authoritative source: ``tee_wrapper.security.validators.canonical_list_message``
and ``ValidatorAuthorizer._verify_and_extract``.

The root key signs ``canonical_list_message(validators, version, not_before)``;
the enclave adopts a dynamic list only if it verifies against the baked-in root
key and its ``version`` is strictly greater than the highest accepted so far
(rollback protection). An optional ``not_before`` issued-at timestamp gates
freshness.

CRITICAL: ``canonical_list_message`` here MUST be byte-identical to the producer.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict


def canonical_list_message(
    validators: list[str], version: int, not_before: int | None = None
) -> bytes:
    """Byte-exact mirror of ``tee_wrapper.security.validators.canonical_list_message``.

    Deterministic bytes the root key signs to authenticate a validator list. The
    validator key set is sorted and lowercased; ``version`` (and the optional
    ``not_before``) are bound so an old-but-validly-signed snapshot cannot be
    replayed.

    Implementation note: ``json.dumps(..., sort_keys=True)`` reorders the
    top-level keys alphabetically ("not_before", "validators", "version"), so the
    serialized order does NOT match the dict's insertion order. The output is
    ``separators=(",", ":")`` compact JSON, UTF-8 encoded.
    """
    body = {
        "version": int(version),
        "validators": sorted(v.lower() for v in validators),
    }
    if not_before is not None:
        body["not_before"] = int(not_before)
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


class ValidatorList(BaseModel):
    """A root-signed validator list as fetched/served on the wire.

    Mirrors the payload ``ValidatorAuthorizer._verify_and_extract`` consumes:
    - ``validators``: list of well-formed 64-hex Ed25519 verify keys.
    - ``version``: monotonic int (rollback protection).
    - ``signature``: hex Ed25519 signature by the root key over
      ``canonical_list_message(validators, version, not_before)``.
    - ``not_before``: optional issued-at timestamp (freshness gate).
    """

    model_config = ConfigDict(extra="allow")

    validators: list[str]
    version: int
    signature: str
    not_before: int | None = None

    def canonical_message(self) -> bytes:
        """The exact bytes the ``signature`` should verify against."""
        return canonical_list_message(self.validators, self.version, self.not_before)


__all__ = ["canonical_list_message", "ValidatorList"]
