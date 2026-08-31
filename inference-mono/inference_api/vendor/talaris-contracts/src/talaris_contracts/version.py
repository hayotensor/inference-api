"""Wire-format version markers for the Talaris TEE protocol.

WIRE_VERSION MUST equal the producer's REPORT_DATA_VERSION
(``tee_wrapper.security.attestation.REPORT_DATA_VERSION``). Bumping the producer
version without bumping this constant means this mirror is out of date.
"""

from __future__ import annotations

# Mirrors tee_wrapper.security.attestation.REPORT_DATA_VERSION ("v6").
WIRE_VERSION = "v6"

# Mirrors tee_wrapper.security.attestation.REPORT_DATA_DOMAIN_TAG (bytes -> str).
REPORT_DATA_DOMAIN_TAG = "tee-wrapper/report-data/v6"

# Mirrors tee_wrapper.security.attestation.REPORT_DATA_FIELDS, in the exact
# order report_data() consumes them (after the domain tag).
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

# Mirrors tee_wrapper.security.miner.BIND_DOMAIN_TAG (bytes).
BIND_DOMAIN_TAG = b"tee-wrapper/bind/v1"

# Mirrors tee_wrapper.security.miner.RECEIPTS_AUTH_DOMAIN_TAG (bytes). DISTINCT from
# BIND_DOMAIN_TAG so a bind signature can never be reinterpreted as a receipts-auth
# proof (and vice versa).
RECEIPTS_AUTH_DOMAIN_TAG = b"tee-wrapper/receipts-auth/v1"

__all__ = [
    "WIRE_VERSION",
    "REPORT_DATA_DOMAIN_TAG",
    "REPORT_DATA_FIELDS",
    "BIND_DOMAIN_TAG",
    "RECEIPTS_AUTH_DOMAIN_TAG",
]
