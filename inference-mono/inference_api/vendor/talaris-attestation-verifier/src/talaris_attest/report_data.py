"""Byte-exact re-implementation of the producer's report_data digest.

THE SINGLE SOURCE OF TRUTH is
``tee_wrapper.security.attestation.report_data`` (and its REPORT_DATA_* constants).
This module mirrors it byte-for-byte so a validator can recompute the digest WITHOUT
importing the producer. If a single byte diverges, every real attestation fails — so the
encoding below is intentionally explicit and matches the producer line-for-line.

Producer (v6), in order, each field length-prefixed with a 4-byte big-endian length
before being fed to a single SHA-256:

    domain_tag           b"tee-wrapper/report-data/v6"   (raw bytes)
    nonce                RAW bytes (bytes.fromhex(doc.nonce))
    identity_fpr         doc.fingerprint                 .encode() UTF-8
    miner_hash           doc.miner or ""                 .encode() UTF-8
    tls_fpr              doc.tls_cert_fingerprint or ""  .encode() UTF-8
    model_hash           doc.model_hash or ""            .encode() UTF-8
    usage_chain_head     doc.usage_chain_head or ""      .encode() UTF-8
    args_hash            doc.args_hash or ""             .encode() UTF-8
    gpu_hash             doc.gpu_hash or ""              .encode() UTF-8
    usage_count          str(int(v)) or b"" if None      (base-10; absent != "0")
    usage_total_tokens   str(int(v)) or b"" if None      (base-10; absent != "0")

CRITICAL encoding rules carried over from the producer:
  * The domain tag is the FIRST length-prefixed field, not a bare prefix.
  * The nonce is committed as RAW bytes, never its hex text.
  * String fields use ``(value or "").encode()`` so None and "" both encode to b"".
  * Integer fields use ``_enc_int_field``: None -> b"" (DISTINCT from 0 -> b"0").
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .docview import DocView
from .errors import UnsupportedReportDataVersion
from .version import SCHEMA_VERSION

# Mirror of tee_wrapper.security.attestation.REPORT_DATA_DOMAIN_TAG. Kept as a literal
# (not imported) so this package has no dependency on the producer. The verify step also
# cross-checks doc.report_data_domain_tag against this value.
REPORT_DATA_DOMAIN_TAG = b"tee-wrapper/report-data/v6"

# Mirror of REPORT_DATA_FIELDS — the ordered, length-prefixed field list (after, and
# including, the domain tag). Published in-band by the producer; we both honor the
# document's copy and assert it equals this canonical order.
REPORT_DATA_FIELDS = (
    "domain_tag",
    "nonce",
    "identity_fpr",
    "miner_hash",
    "tls_fpr",
    "model_hash",
    "usage_chain_head",
    "args_hash",
    "gpu_hash",
    "usage_count",
    "usage_total_tokens",
)


def _enc_int_field(v: Optional[int]) -> bytes:
    """Encode an optional integer report_data field: absent -> b"" (distinct from 0).

    Byte-exact mirror of the producer's ``_enc_int_field``.
    """
    return b"" if v is None else str(int(v)).encode()


def recompute(doc: DocView) -> bytes:
    """Recompute the 32-byte report_data digest from the document's published fields.

    ``doc`` is a :class:`DocView` over the raw attestation JSON (or a model_dump'd
    talaris_contracts.AttestationDocument). Raises UnsupportedReportDataVersion if the
    document advertises a report_data_version we do not implement (fail closed — never
    silently accept an unknown framing).
    """
    version = doc.report_data_version
    if version != SCHEMA_VERSION:
        raise UnsupportedReportDataVersion(
            f"report_data_version {version!r} is not supported by this verifier "
            f"(expected {SCHEMA_VERSION!r}); refusing to recompute"
        )

    # nonce is committed as RAW bytes, not its hex text.
    nonce_raw = bytes.fromhex(doc.nonce)

    # Build the field tuple in the EXACT producer order and encoding.
    fields = (
        REPORT_DATA_DOMAIN_TAG,
        nonce_raw,
        doc.identity_fpr.encode(),
        (doc.miner_hash or "").encode(),
        (doc.tls_fpr or "").encode(),
        (doc.model_hash or "").encode(),
        (doc.usage_chain_head or "").encode(),
        (doc.args_hash or "").encode(),
        (doc.gpu_hash or "").encode(),
        _enc_int_field(doc.usage_count),
        _enc_int_field(doc.usage_total_tokens),
    )

    h = hashlib.sha256()
    for f in fields:
        h.update(len(f).to_bytes(4, "big"))
        h.update(f)
    return h.digest()
