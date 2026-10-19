# talaris-attestation-verifier

The shared, security-critical attestation **verifier** for the Talaris system — the
counterpart to the TEE container's attestation **producer**
(`tee_wrapper.security.attestation`), which is the single source of truth.

A relying party (validator) uses this package to independently verify an attestation
document returned by a TEE wrapper's `GET /attestation?nonce=<hex>` endpoint, and then to
build a TLS client pinned to the exact cert the enclave attested.

It deliberately does **not** import the producer (`tee_wrapper`) or `talaris_contracts`,
keeping dependencies acyclic. Instead it **re-implements** the producer's `report_data`
digest and TLS-SPKI binding byte-for-byte. Faithfulness to those two is the #1 priority:
a single wrong byte makes every real attestation fail.

## Install

```bash
pip install -e .                 # core: cryptography, pynacl, httpx
pip install -e .[nvidia-local]   # NvidiaLocalBackend (nv-attestation-sdk)
pip install -e .[intel-dcap]     # IntelPcsTdxBackend real chain (DCAP libs)
```

## Usage

```python
from talaris_attest import (
    verify_attestation, ExpectedClaims, ModelAllowlistEntry,
    build_pinned_client,
)

nonce = secrets.token_bytes(32)
doc = httpx.get(url, params={"nonce": nonce.hex()}, verify=False).json()

expected = ExpectedClaims(               # DEFAULTS ARE PRODUCTION-SAFE
    required_modes=frozenset({"nvidia"}),
    require_gpu_evidence=True,
    require_tdx_quote=True,
    model_allowlist=[ModelAllowlistEntry(model_hash="...", args_hash="...")],
)

result = verify_attestation(doc, expected, nonce)
if not result.ok:
    raise SystemExit(result.summary())

client = build_pinned_client(result, base_url=url, bearer=token)  # leaf cert pinned
```

`doc` may be a plain dict (raw `/attestation` JSON) **or** anything exposing
`.model_dump()` (e.g. a `talaris_contracts.AttestationDocument`) — it is duck-typed.

## What it verifies (each → one `CheckResult`)

1. **mode gate** — `doc.mode` in `required_modes`, or (`dev` + `allow_insecure_dev`).
   Unknown mode fails closed. *(first of two independent dev gates)*
2. **nonce echo** — `bytes.fromhex(doc.nonce) == nonce`.
3. **report_data** — recompute the v6 digest from the document's fields and compare to
   `doc.report_data`. An unknown `report_data_version` fails (never silently accepted).
4. **GPU evidence** — dispatch `evidence.gpu_evidence` to the NVIDIA backend; it verifies
   the signing chain **and** that the evidence nonce == `doc.report_data`.
5. **TDX quote** — dispatch `evidence.tdx_quote` to the Intel backend; verify the
   PCS/DCAP chain **and** that the quote's `report_data[:64]` echoes `doc.report_data`.
5b. **measurement pin** (`measurement_pin`) — when `tdx_measurement_allowlist` is set
   (`TdxMeasurementEntry(mrtd=..., rtmr0..rtmr3=..., label=...)`, or
   `load_measurement_allowlist(json_or_@file)`), the **hardware-verified** TDX
   measurements must match a published build; a missing/unverified quote or a stub backend
   fails closed. Unset: informational (and says the enclave software is NOT pinned), unless
   `require_measurement_pin=True`, which then fails. Without a pin, any software in a
   genuine TD passes. Note that MRTD/RTMR0-2 measure the TD boot chain, so they bind the
   enclave's code only when it ships as a measured CVM image (not a container started
   later inside the TD). Configure through `IntelPolicy(allowed_measurements=...,
   require_measurement_pin=...)`.
6. **gpu_hash cross-check** — `hash_gpu_uuids(doc.gpu_uuids) == doc.gpu_hash` **and**
   `set(doc.gpu_uuids)` ⊆ NVIDIA-attested UUIDs. Skipped (informational) when `gpu_hash`
   is `None` (engine not ready).
7. **model/args allowlist** — when set, some entry must match `doc.model_hash`
   (and pinned `args_hash`/`gpu_hash`).
8. **expected miner_hash** — when set, `doc.miner` must equal it.
9. **TLS pin** — verify the enclave Ed25519 signature over
   `b"tee-wrapper/tls-spki/v1" + served-cert-SPKI-DER` under the enclave verify key, and
   `sha256(served-cert-DER) == doc.tls_cert_fingerprint`. `build_pinned_client` enforces
   this live at connection time.

`VerificationResult.ok` is `True` iff every **required** check passed. Informational
checks (e.g. `gpu_hash` absent) never fail the result.

## Two independent dev gates

`dev` mode (where the only "evidence" is the enclave's own Ed25519 self-signature over
`report_data`) is insecure and must be rejected in production. Two gates enforce this:

1. the **mode gate** (`required_modes` / `allow_insecure_dev`); and
2. the **`backend_insecure_gate`**: the stub backends are `is_secure = False` and are
   refused unless `allow_insecure_dev` is set.

## Backends — real vs skeleton

| Backend | Real (enforced & unit-tested) | TODO skeleton |
|---|---|---|
| `StubGpuBackend` / `StubTdxBackend` | dev self-signature / absent-quote acceptance; `is_secure=False` | — (never validates HW chains, by design) |
| `IntelPcsTdxBackend` | v4/v5 quote parsing + `report_data[:64]` echo (mirrors `tdx.py`) | PCS/DCAP signature-chain verification (`[intel-dcap]`) — fails closed with `require_chain=True` |
| `NvidiaNrasBackend` | request structure + EAT nonce-binding (mockable transport) | EAT JWS signature verification vs NRAS JWKS; exact NRAS endpoint/auth — fails closed unless `trust_eat_signature=True` (test-only) |
| `NvidiaLocalBackend` | nonce-binding contract (mirrors NRAS) | `nv_attestation_sdk` flow (`[nvidia-local]`) — fails closed until implemented |

## Tests

```bash
PYTHONPATH=/path/to/inference-subnet-miner/src python -m pytest tests -q
```

The test suite generates **genuine** dev-mode attestation documents by running the
producer in-process (FastAPI `TestClient` over `tee_wrapper.app.create_app`) and verifies
them, so the `report_data` / TLS-binding re-implementations are checked against the real
source of truth, not a mock.
