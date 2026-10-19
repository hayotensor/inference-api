"""talaris-contracts: shared wire-format / SDK for the Talaris system.

Pydantic v2 mirrors of the TEE container's wire formats, byte-exact crypto/seal
helpers, and a thin (sync + async) TEE HTTP client. This package is a
security-critical mirror of the producer (``tee_wrapper``); models are kept
additively forward-compatible (``extra="allow"``).
"""

from __future__ import annotations

from .attestation import (
    AttestationDocument,
    GpuEvidence,
    PublicBundle,
    TdxMeasurements,
    TdxQuote,
    report_data_inputs,
)
from .bind import (
    BindRequest,
    RECEIPTS_AUTH_DOMAIN_TAG,
    bind_message,
    receipts_auth_message,
)
from .allowlist import (
    ALLOWLIST_KIND,
    AllowlistEntry,
    ModelAllowlistArtifact,
    canonical_allowlist_message,
)
from .crypto import (
    build_provision_request,
    miner_hash,
    seal_credentials,
    sign_miner_stats,
    sign_model_allowlist,
    sign_validator_list,
    verify_miner_stats,
    verify_model_allowlist,
)
from .miner_stats import (
    MINER_STATS_KIND,
    MinerEpochStatsArtifact,
    MinerStatsEntry,
    canonical_miner_stats_message,
)
from .identity import ed25519_peer_id, ed25519_pubkey_from_peer_id
from .provision import Credentials, ProvisionRequest
from .registration import (
    REGISTRATION_DOMAIN_TAG,
    HostedModel,
    MinerHealth,
    SelfRegistration,
    TeeDescriptor,
    registration_message_for,
    registration_miner_hash,
    registration_signing_message,
    sign_registration_ed25519,
    verify_registration_signature,
)
from .tee_client import AsyncTeeClient, TeeClient
from .usage import (
    USAGE_HEAD_DOMAIN,
    InferenceTokenUsage,
    UsageReceipt,
    usage_head_signing_message,
)
from .validator_list import ValidatorList, canonical_list_message
from .version import (
    BIND_DOMAIN_TAG,
    REPORT_DATA_DOMAIN_TAG,
    REPORT_DATA_FIELDS,
    WIRE_VERSION,
)

__version__ = "0.1.0"

__all__ = [
    # version / constants
    "WIRE_VERSION",
    "REPORT_DATA_DOMAIN_TAG",
    "REPORT_DATA_FIELDS",
    "BIND_DOMAIN_TAG",
    # attestation
    "AttestationDocument",
    "PublicBundle",
    "GpuEvidence",
    "TdxQuote",
    "TdxMeasurements",
    "report_data_inputs",
    # provision
    "Credentials",
    "ProvisionRequest",
    # bind
    "BindRequest",
    "bind_message",
    "receipts_auth_message",
    "RECEIPTS_AUTH_DOMAIN_TAG",
    # registration
    "SelfRegistration",
    "HostedModel",
    "TeeDescriptor",
    "MinerHealth",
    "REGISTRATION_DOMAIN_TAG",
    "registration_signing_message",
    "registration_message_for",
    "verify_registration_signature",
    "sign_registration_ed25519",
    "registration_miner_hash",
    # identity mapping (platform key <-> libp2p peer id <-> chain node)
    "ed25519_peer_id",
    "ed25519_pubkey_from_peer_id",
    # usage
    "InferenceTokenUsage",
    "UsageReceipt",
    "USAGE_HEAD_DOMAIN",
    "usage_head_signing_message",
    # validator list
    "ValidatorList",
    "canonical_list_message",
    # model allowlist
    "ALLOWLIST_KIND",
    "AllowlistEntry",
    "ModelAllowlistArtifact",
    "canonical_allowlist_message",
    # miner stats
    "MINER_STATS_KIND",
    "MinerStatsEntry",
    "MinerEpochStatsArtifact",
    "canonical_miner_stats_message",
    # crypto
    "seal_credentials",
    "build_provision_request",
    "sign_validator_list",
    "sign_model_allowlist",
    "verify_model_allowlist",
    "sign_miner_stats",
    "verify_miner_stats",
    "miner_hash",
    # client
    "TeeClient",
    "AsyncTeeClient",
]
