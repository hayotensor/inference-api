# talaris-contracts

Shared wire-format / SDK package for the Talaris system: Pydantic v2 mirrors of
the TEE container's wire formats, byte-exact crypto/seal helpers, and a thin
(sync + async) TEE HTTP client.

This package is a **security-critical mirror** of the producer
(`tee_wrapper` in `inference-subnet-miner`). The producer source is the single
source of truth; the models here mirror it field-for-field and are kept
additively forward-compatible (`extra="allow"`) so new producer fields survive a
parse → `model_dump()` round-trip.

## What's in here

| Module | Contents |
| --- | --- |
| `version` | `WIRE_VERSION` (= producer `REPORT_DATA_VERSION` = `"v6"`), domain tags, `REPORT_DATA_FIELDS` |
| `attestation` | `AttestationDocument` (+ `PublicBundle`, loose `GpuEvidence`/`TdxQuote`/`TdxMeasurements`), `report_data_inputs()` |
| `provision` | `Credentials{admin_token, inference_keys}`, `ProvisionRequest{validator_pubkey, sealed, signature}` |
| `bind` | `BindRequest{miner_pubkey, signature, key_type?}`, `bind_message()` |
| `registration` | `SelfRegistration`, `HostedModel`, `TeeDescriptor`, `MinerHealth` |
| `usage` | `InferenceTokenUsage` / `UsageReceipt` |
| `validator_list` | `ValidatorList`, `canonical_list_message()` (byte-exact mirror) |
| `crypto` | `seal_credentials`, `build_provision_request`, `sign_validator_list`, `miner_hash` |
| `tee_client` | `TeeClient`, `AsyncTeeClient` |

## Critical invariants

1. **`AttestationDocument`** mirrors the producer dataclass field-for-field
   (mode, nonce, report_data, public_bundle, miner, evidence,
   tls_cert_fingerprint, model_hash, usage_chain_head, args_hash, gpu_hash,
   gpu_uuids, usage_count, usage_total_tokens, report_data_version,
   report_data_domain_tag, report_data_fields) **plus** the three router-injected
   top-level keys (`boot_nonce`, `fingerprint`, `tls_pubkey_signature`).
   `extra="allow"` for additive forward-compat. `report_data` is **not**
   recomputed here (that lives in the verifier package); `report_data_inputs(doc)`
   exposes the ordered field map for a verifier.
2. **`canonical_list_message(validators, version, not_before=None)`** is
   byte-identical to `tee_wrapper.security.validators.canonical_list_message`.
   `sort_keys=True` reorders top-level keys alphabetically.
3. **`build_provision_request`** reproduces the producer seal+sign flow:
   `SealedBox(PublicKey(...)).encrypt(json.dumps(creds).encode())` and
   `signing_key.sign(sealed).signature.hex()`. (The sealed ciphertext is
   non-deterministic per call; the invariants are that it unseals back to the
   creds and the signature verifies over the raw sealed bytes.)

## TEE client

```python
from talaris_contracts import TeeClient, Credentials

with TeeClient("https://tee.example") as c:
    doc = c.get_attestation(nonce_bytes)            # GET /attestation?nonce=hex
    c.provision(creds, doc.public_bundle.sealing_key, platform_signing_key)
    resp = c.chat_completions(inference_token, payload)   # Bearer auth
```

A pre-built (e.g. TLS-pinned) `httpx.Client` / `httpx.AsyncClient` can be
injected via `client=...`; an injected client is **not** closed by the wrapper.
Bearer / admin tokens are only ever placed in the `Authorization` header, never
logged.

## Development / tests

Dependencies: `pydantic>=2`, `pynacl`, `httpx` (+ `pytest` for dev). The test
suite additionally imports `tee_wrapper` (the producer) to generate real
fixtures and assert byte-equality, so the producer source must be importable.

```bash
pip install -e .[dev]
PYTHONPATH=/path/to/inference-subnet-miner/src pytest tests -q
```
