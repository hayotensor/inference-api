"""``GET /attest`` — the documented, fetchable pin + verification recipe.

Publishes the platform's Ed25519 VERIFY key (never the signing key) plus, for
each artifact this API signs, the canonical-message "kind" and the function a
third party calls to verify it independently -- so nobody has to trust this
API's own ``"attested": true`` / self-reported claims. Verification requires
nothing more than PyNaCl (or the shared, openly-readable ``talaris_contracts``
/ ``inference_api.attestation`` modules) plus the pinned key returned here.

Public and UNAUTHENTICATED, for the same reason ``/allowlist`` and
``/subnet/stats/*`` are: the whole point is that a caller who does NOT trust
this server can still verify its claims, so gating this endpoint behind API
auth would defeat its purpose. See ``main.py``'s public-endpoint throttle,
which already covers any path this router adds.
"""

from __future__ import annotations

from fastapi import APIRouter

from talaris_contracts import ALLOWLIST_KIND, MINER_STATS_KIND

from inference_api.attestation import INFERENCE_RECEIPT_KIND
from inference_api.provisioner.key import provisioner_verify_key_hex

router = APIRouter(tags=["attestation"])


@router.get("/attest")
async def get_attestation_pin() -> dict:
    """The platform's pinned verify key + how to verify everything it signs.

    A relying party fetches this ONCE, pins ``platform_pubkey`` out of band
    (config, code, on-chain record -- not re-fetched per verification and
    never substituted with an artifact's own ``signer_pubkey`` field), and
    then verifies every signed artifact this API serves against that single
    trust anchor.
    """
    platform_pubkey = provisioner_verify_key_hex()
    return {
        "platform_pubkey": platform_pubkey,
        "signing_scheme": "ed25519",
        "note": (
            "This is the SAME Ed25519 key that signs every artifact listed "
            "below. Pin it out of band and verify each artifact's signature "
            "against THIS value -- never trust an artifact's own embedded "
            "signer_pubkey field, which is self-asserted and carried only "
            "for convenience/diagnostics."
        ),
        "artifacts": {
            "model_allowlist": {
                "endpoint": "GET /allowlist",
                "kind": ALLOWLIST_KIND,
                "verify": "talaris_contracts.verify_model_allowlist(artifact, platform_pubkey)",
                "note": (
                    "The platform-approved (model_id -> model_hash) set. A "
                    "'demo' labeled entry is a non-production placeholder; "
                    "real entries are populated either by operator approval "
                    "or, on first successful hardware attestation of a model "
                    "with no prior pin, automatically (label='observed-attested') "
                    "-- see inference_api.allowlist_seed.sync_observed_model_allowlist."
                ),
            },
            "miner_stats": {
                "endpoint": "GET /subnet/stats/{epoch} or /subnet/stats/latest",
                "kind": MINER_STATS_KIND,
                "verify": "talaris_contracts.verify_miner_stats(artifact, platform_pubkey)",
            },
            "inference_receipt": {
                "field": (
                    "POST /v1/inference response body: .attestation "
                    "(present iff .attested is true); mirrored as the "
                    "additive .talaris_attestation field on "
                    "POST /v1/chat/completions"
                ),
                "kind": INFERENCE_RECEIPT_KIND,
                "verify": "inference_api.attestation.verify_inference_receipt(receipt, platform_pubkey)",
                "note": (
                    "Binds ONE specific inference response to the serving "
                    "enclave's most recently hardware-verified TEE "
                    "attestation (model_hash, enclave TEE verify key, TLS "
                    "leaf fingerprint, attestation mode and verified-at "
                    "time). This is not a raw per-request hardware quote -- "
                    "the hardware quote itself (NVIDIA GPU evidence / Intel "
                    "TDX quote) is checked once, at provisioning time, via "
                    "talaris_attest.verify_attestation, which is a public, "
                    "independently auditable package."
                ),
            },
        },
    }
