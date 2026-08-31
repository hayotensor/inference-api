"""Usage / token-accounting wire format.

Mirrors the producer's per-request usage row (``tee_wrapper.db.store`` /
schema ``requests`` table: ``ts, inference_key_id, model, prompt_tokens,
completion_tokens, total_tokens, ...``) plus the cumulative, tamper-evident
chain fields the attestation document binds into report_data v6
(``usage_chain_head``, ``usage_count``, ``usage_total_tokens``).

These let a verifier correlate a signed per-request receipt with the cumulative
counters bound into a hardware-attested ``report_data`` digest.

Also home to :func:`usage_head_signing_message` — the canonical bytes the enclave
signs over its cumulative usage head, and therefore the single source of truth any
consumer must use to verify the ``head_signature`` served by
``GET /usage/receipts``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# Domain-separation tag the enclave signs the usage head under. MUST stay
# byte-identical to the producer's ``tee_wrapper.routers.usage._USAGE_HEAD_DOMAIN``
# (the authoritative source); the test-suite asserts the two are equal whenever the
# producer is importable.
USAGE_HEAD_DOMAIN = b"tee-wrapper/usage-head/v1"


def usage_head_signing_message(
    usage_count: int, usage_chain_head: str, epoch: float | int | None
) -> bytes:
    """Recompute the canonical, domain-tagged bytes the enclave signs over the head.

    BYTE-EXACT mirror of the producer's
    ``tee_wrapper.routers.usage._head_signing_message``: each field is
    length-prefixed (4-byte big-endian) so the variable-length fields can never
    collide via concatenation. ``epoch`` follows the producer encoding: ``""`` when
    ``None``, else ``repr(epoch)`` (so a float ``5.0`` becomes ``"5.0"``).

    A consumer reconstructs these bytes from the published
    ``(usage_count, usage_chain_head, epoch)`` and verifies the enclave's
    ``head_signature`` against the attested verify key.
    """
    fields = (
        USAGE_HEAD_DOMAIN,
        str(usage_count).encode(),
        (usage_chain_head or "").encode(),
        ("" if epoch is None else repr(epoch)).encode(),
    )
    out = bytearray()
    for field in fields:
        out += len(field).to_bytes(4, "big")
        out += field
    return bytes(out)


class InferenceTokenUsage(BaseModel):
    """Per-request token usage with the cumulative chain context.

    Mirrors a producer usage row's accounting fields plus the cumulative
    counters bound into report_data v6:
    - ``inference_key_id``: which provisioned inference key served the request.
    - ``request_id`` / ``model``: request identity and served model.
    - ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``: per-request.
    - ``usage_chain_head``: tamper-evident hash-chain head AFTER this request.
    - ``usage_count`` / ``usage_total_tokens``: cumulative counters bound into
      the hardware-signed report_data (rollback/truncation-evident).
    - ``ts``: producer timestamp (REAL seconds).
    """

    model_config = ConfigDict(extra="allow")

    inference_key_id: str | None = None
    request_id: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    usage_chain_head: str | None = None
    usage_count: int | None = None
    usage_total_tokens: int | None = None
    ts: float | None = None


# A usage receipt is structurally identical to the per-request usage record; the
# alias documents intent (a signed/served receipt vs. an internal record).
UsageReceipt = InferenceTokenUsage


__all__ = [
    "USAGE_HEAD_DOMAIN",
    "InferenceTokenUsage",
    "UsageReceipt",
    "usage_head_signing_message",
]
