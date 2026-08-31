"""Schema version this verifier understands.

This MUST track the producer's REPORT_DATA_VERSION
(tee_wrapper.security.attestation.REPORT_DATA_VERSION). The producer is the single
source of truth; this string is the version of report_data framing we know how to
recompute. A document advertising a different report_data_version is rejected (we do not
silently accept an unknown framing).
"""

from __future__ import annotations

SCHEMA_VERSION = "v6"
