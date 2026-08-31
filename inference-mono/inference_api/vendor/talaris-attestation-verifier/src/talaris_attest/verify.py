"""verify_attestation — the orchestrator.

Runs the ordered verification steps, each appending exactly one CheckResult, and returns
a VerificationResult whose ``ok`` is True iff every REQUIRED check passed. Steps never
abort the run on failure — they record and continue — so the caller gets a complete
report. The only hard stop is a structurally unusable document (raises a verifier error).

Two INDEPENDENT dev gates protect production:
  1. the mode gate (step 1): doc.mode must be in required_modes, OR (dev + allow_insecure_dev).
  2. the backend_insecure_gate (before steps 4/5): a stub/insecure backend is refused
     unless allow_insecure_dev is set.
"""

from __future__ import annotations

from typing import Optional

from .claims import ExpectedClaims
from .docview import DocView
from .errors import MalformedDocument, UnsupportedReportDataVersion
from .gpu import hash_gpu_uuids
from .report_data import REPORT_DATA_DOMAIN_TAG, REPORT_DATA_FIELDS, recompute
from .result import CheckResult, VerificationResult
from .tls import verify_tls_pin
from .version import SCHEMA_VERSION

# Modes this verifier recognizes. An unrecognized mode fails the gate closed.
_KNOWN_MODES = frozenset({"dev", "nvidia"})


def _default_gpu_backend(expected: ExpectedClaims):
    from .backends.nvidia_nras import NvidiaNrasBackend
    from .backends.stub import StubGpuBackend

    if expected.nvidia_backend is not None:
        return expected.nvidia_backend
    return StubGpuBackend() if expected.is_insecure_dev() else NvidiaNrasBackend()


def _default_tdx_backend(expected: ExpectedClaims):
    from .backends.intel_pcs import IntelPcsTdxBackend
    from .backends.stub import StubTdxBackend

    if expected.tdx_backend is not None:
        return expected.tdx_backend
    return StubTdxBackend() if expected.is_insecure_dev() else IntelPcsTdxBackend()


def verify_attestation(
    doc: object,
    expected: ExpectedClaims,
    nonce: bytes,
) -> VerificationResult:
    """Verify ``doc`` against ``expected`` for the freshness ``nonce``.

    ``doc`` may be a plain dict (raw /attestation JSON) or anything with .model_dump().
    """
    view = DocView(doc)
    result = VerificationResult()
    result.mode = view.mode
    result.tls_cert_fingerprint = view.tls_fpr

    # === Step 1: mode gate (first dev gate) ============================
    _check_mode(view, expected, result)

    # === Step 2: nonce echo ============================================
    _check_nonce(view, nonce, result)

    # === Step 3: recompute report_data =================================
    recomputed_ok = _check_report_data(view, result)

    # The remaining hardware/cross-checks bind to report_data; if we could not recompute
    # it we still run them against the document's claimed report_data, but a failed step 3
    # already makes the result not-ok.
    try:
        report_data_bytes = bytes.fromhex(view.report_data)
    except (ValueError, MalformedDocument):
        report_data_bytes = b""
    report_data_hex = view.report_data if report_data_bytes else ""
    result.report_data_hex = report_data_hex or None

    # === backend selection + second dev gate ===========================
    gpu_backend = _default_gpu_backend(expected)
    tdx_backend = _default_tdx_backend(expected)
    _check_backend_gate(expected, gpu_backend, tdx_backend, result)

    # === Step 4: NVIDIA GPU evidence ===================================
    measured_uuids: tuple[str, ...] = ()
    if expected.require_gpu_evidence:
        measured_uuids = _check_gpu_evidence(
            view, expected, gpu_backend, report_data_hex, result
        )
        result.measured_gpu_uuids = measured_uuids

    # === Step 5: Intel TDX quote =======================================
    if expected.require_tdx_quote:
        _check_tdx_quote(view, tdx_backend, report_data_bytes, result)

    # === Step 6: gpu_uuids <-> gpu_hash cross-check ====================
    _check_gpu_hash(view, measured_uuids, expected, result)

    # === Step 7: model/args allowlist ==================================
    _check_model_allowlist(view, expected, result)

    # === Step 8: expected miner_hash ===================================
    _check_miner_hash(view, expected, result)

    # === Step 9: TLS pin ===============================================
    _check_tls_pin(view, expected, result)

    return result


# --------------------------------------------------------------------------- #
# Individual steps
# --------------------------------------------------------------------------- #

def _check_mode(view: DocView, expected: ExpectedClaims, result: VerificationResult) -> None:
    mode = view.mode
    if mode not in _KNOWN_MODES:
        result.add(CheckResult(
            "mode", False,
            f"unknown attestation mode {mode!r} (fail closed)",
            detail={"mode": mode, "known": sorted(_KNOWN_MODES)},
        ))
        return
    allowed = mode in expected.required_modes
    dev_allowed = mode == "dev" and expected.allow_insecure_dev
    if allowed or dev_allowed:
        reason = (
            f"mode {mode!r} allowed"
            + (" via allow_insecure_dev" if (dev_allowed and not allowed) else "")
        )
        result.add(CheckResult("mode", True, reason, detail={"mode": mode}))
    else:
        result.add(CheckResult(
            "mode", False,
            f"mode {mode!r} not in required_modes {sorted(expected.required_modes)} "
            f"and dev override not enabled",
            detail={"mode": mode, "required_modes": sorted(expected.required_modes)},
        ))


def _check_nonce(view: DocView, nonce: bytes, result: VerificationResult) -> None:
    try:
        doc_nonce = bytes.fromhex(view.nonce)
    except (ValueError, MalformedDocument) as exc:
        result.add(CheckResult("nonce_echo", False, f"document nonce invalid: {exc}"))
        return
    if doc_nonce == nonce:
        result.add(CheckResult(
            "nonce_echo", True, "document nonce echoes the requested nonce"
        ))
    else:
        result.add(CheckResult(
            "nonce_echo", False,
            "document nonce does not echo the requested nonce (stale/replayed)",
            detail={"expected": nonce.hex(), "got": view.nonce},
        ))


def _check_report_data(view: DocView, result: VerificationResult) -> bool:
    # First, validate the in-band framing matches what we know how to recompute.
    version = view.report_data_version
    if version != SCHEMA_VERSION:
        result.add(CheckResult(
            "report_data", False,
            f"report_data_version {version!r} unsupported (expected {SCHEMA_VERSION!r}); "
            f"refusing to silently accept",
            detail={"report_data_version": version},
        ))
        return False
    domain_tag = view.report_data_domain_tag
    if domain_tag is not None and domain_tag != REPORT_DATA_DOMAIN_TAG.decode():
        result.add(CheckResult(
            "report_data", False,
            f"report_data_domain_tag {domain_tag!r} != expected "
            f"{REPORT_DATA_DOMAIN_TAG.decode()!r}",
        ))
        return False
    fields = view.report_data_fields
    if fields is not None and tuple(fields) != REPORT_DATA_FIELDS:
        result.add(CheckResult(
            "report_data", False,
            "report_data_fields order does not match the expected v6 field list",
            detail={"got": list(fields), "expected": list(REPORT_DATA_FIELDS)},
        ))
        return False

    try:
        recomputed = recompute(view).hex()
    except UnsupportedReportDataVersion as exc:
        result.add(CheckResult("report_data", False, str(exc)))
        return False
    except (MalformedDocument, ValueError) as exc:
        result.add(CheckResult(
            "report_data", False, f"could not recompute report_data: {exc}"
        ))
        return False

    claimed = view.report_data
    if recomputed == claimed:
        result.add(CheckResult(
            "report_data", True,
            "recomputed report_data matches the document's report_data",
        ))
        return True
    result.add(CheckResult(
        "report_data", False,
        "recomputed report_data does NOT match the document (field tampering or "
        "encoding mismatch)",
        detail={"recomputed": recomputed, "claimed": claimed},
    ))
    return False


def _check_backend_gate(
    expected: ExpectedClaims,
    gpu_backend,
    tdx_backend,
    result: VerificationResult,
) -> None:
    """Second dev gate: refuse an insecure (stub) backend in production mode."""
    if expected.is_insecure_dev():
        result.add(CheckResult(
            "backend_insecure_gate", True,
            "insecure-dev mode: stub/insecure backends permitted",
            required=False,
        ))
        return
    insecure = []
    if getattr(gpu_backend, "is_secure", False) is not True:
        insecure.append(f"gpu={type(gpu_backend).__name__}")
    if getattr(tdx_backend, "is_secure", False) is not True:
        insecure.append(f"tdx={type(tdx_backend).__name__}")
    if insecure:
        result.add(CheckResult(
            "backend_insecure_gate", False,
            "insecure/stub backend selected in production mode (allow_insecure_dev is "
            f"False): {', '.join(insecure)}",
            detail={"insecure_backends": insecure},
        ))
    else:
        result.add(CheckResult(
            "backend_insecure_gate", True,
            "all selected backends are hardware (secure) backends",
        ))


def _check_gpu_evidence(
    view: DocView,
    expected: ExpectedClaims,
    gpu_backend,
    report_data_hex: str,
    result: VerificationResult,
) -> tuple[str, ...]:
    evidence = view.evidence
    gpu_evidence = evidence.get("gpu_evidence")

    # For the stub backend, wrap the dev self-signature material it needs.
    if getattr(gpu_backend, "is_secure", True) is False:
        from .backends.stub import DevGpuEvidence
        try:
            rd = bytes.fromhex(view.report_data)
            vk = view.verify_key_bytes
        except (ValueError, MalformedDocument) as exc:
            result.add(CheckResult(
                "gpu_evidence", False,
                f"cannot build dev evidence (bad report_data/verify_key): {exc}",
            ))
            return ()
        gpu_evidence = DevGpuEvidence(
            verify_key_bytes=vk,
            dev_signature_hex=view.dev_signature_hex,
            report_data=rd,
            gpu_uuids=tuple(view.gpu_uuids),
            raw_evidence=gpu_evidence,
        )
    elif gpu_evidence is None:
        result.add(CheckResult(
            "gpu_evidence", False,
            "GPU evidence required but document has no evidence.gpu_evidence",
        ))
        return ()

    try:
        backend_result = gpu_backend.verify(gpu_evidence, report_data_hex)
    except Exception as exc:  # noqa: BLE001 - a backend crash must not crash verification
        result.add(CheckResult(
            "gpu_evidence", False,
            f"GPU backend raised: {type(exc).__name__}: {exc}",
        ))
        return ()

    if backend_result.verified:
        result.add(CheckResult(
            "gpu_evidence", True,
            backend_result.reason or "GPU evidence verified and nonce-bound",
            detail={"measured_gpu_uuids": list(backend_result.measured_gpu_uuids)},
        ))
        return tuple(backend_result.measured_gpu_uuids)
    result.add(CheckResult(
        "gpu_evidence", False,
        backend_result.reason or "GPU evidence verification failed",
    ))
    return ()


def _check_tdx_quote(
    view: DocView,
    tdx_backend,
    report_data_bytes: bytes,
    result: VerificationResult,
) -> None:
    evidence = view.evidence
    # The producer stores the quote under evidence["tdx_quote"] (dict or None).
    quote = evidence.get("tdx_quote")
    try:
        backend_result = tdx_backend.verify(quote, report_data_bytes)
    except Exception as exc:  # noqa: BLE001
        result.add(CheckResult(
            "tdx_quote", False,
            f"TDX backend raised: {type(exc).__name__}: {exc}",
        ))
        return
    if backend_result.verified:
        result.add(CheckResult(
            "tdx_quote", True,
            backend_result.reason or "TDX quote verified and report_data echoed",
            detail={"measurements": backend_result.measurements},
        ))
    else:
        result.add(CheckResult(
            "tdx_quote", False,
            backend_result.reason or "TDX quote verification failed",
        ))


def _check_gpu_hash(
    view: DocView,
    measured_uuids: tuple[str, ...],
    expected: ExpectedClaims,
    result: VerificationResult,
) -> None:
    gpu_hash = view.gpu_hash
    if gpu_hash is None:
        # Engine not ready / no pinning happened: informational, not a failure.
        result.add(CheckResult(
            "gpu_hash", True,
            "gpu_hash is absent (engine not ready / no GPU pinning) — cross-check skipped",
            required=False,
            detail={"gpu_uuids": view.gpu_uuids},
        ))
        return

    recomputed = hash_gpu_uuids(view.gpu_uuids)
    if recomputed != gpu_hash:
        result.add(CheckResult(
            "gpu_hash", False,
            "recomputed hash_gpu_uuids(gpu_uuids) does not match doc.gpu_hash",
            detail={"recomputed": recomputed, "claimed": gpu_hash,
                    "gpu_uuids": view.gpu_uuids},
        ))
        return

    # Subset check against the NVIDIA-attested UUIDs (only when GPU evidence ran AND
    # returned measured UUIDs).
    if expected.require_gpu_evidence and measured_uuids:
        pinned = set(view.gpu_uuids)
        attested = set(measured_uuids)
        if not pinned.issubset(attested):
            result.add(CheckResult(
                "gpu_hash", False,
                "pinned gpu_uuids are not a subset of the NVIDIA-attested UUIDs "
                "(serving on un-attested GPU?)",
                detail={"pinned": sorted(pinned), "attested": sorted(attested)},
            ))
            return
        result.add(CheckResult(
            "gpu_hash", True,
            "gpu_hash matches and pinned UUIDs are a subset of attested UUIDs",
            detail={"gpu_uuids": view.gpu_uuids},
        ))
        return

    # gpu_hash matches but we have no attested set to subset-check against.
    result.add(CheckResult(
        "gpu_hash", True,
        "gpu_hash matches recomputed hash (no attested UUID set to subset-check)",
        required=False,
        detail={"gpu_uuids": view.gpu_uuids},
    ))


def _check_model_allowlist(
    view: DocView, expected: ExpectedClaims, result: VerificationResult
) -> None:
    allowlist = expected.model_allowlist
    if not allowlist:
        result.add(CheckResult(
            "model_allowlist", True,
            "no model allowlist configured — model/args not constrained",
            required=False,
        ))
        return
    model_hash = view.model_hash
    if model_hash is None:
        result.add(CheckResult(
            "model_allowlist", False,
            "model allowlist set but doc.model_hash is absent (engine not ready / no "
            "model attested)",
            detail={"model_hash": None},
        ))
        return
    for entry in allowlist:
        if entry.matches(model_hash, view.args_hash, view.gpu_hash):
            result.add(CheckResult(
                "model_allowlist", True,
                f"model matches allowlist entry {entry.label or entry.model_hash!r}",
                detail={"model_hash": model_hash},
            ))
            return
    result.add(CheckResult(
        "model_allowlist", False,
        "doc.model_hash (and any pinned args/gpu) matches no allowlist entry",
        detail={"model_hash": model_hash, "args_hash": view.args_hash,
                "gpu_hash": view.gpu_hash},
    ))


def _check_miner_hash(
    view: DocView, expected: ExpectedClaims, result: VerificationResult
) -> None:
    want = expected.expected_miner_hash
    if want is None:
        result.add(CheckResult(
            "miner_hash", True, "no expected miner_hash configured", required=False
        ))
        return
    got = view.miner_hash
    if got == want:
        result.add(CheckResult("miner_hash", True, "doc.miner matches expected"))
    else:
        result.add(CheckResult(
            "miner_hash", False,
            "doc.miner does not match expected_miner_hash",
            detail={"expected": want, "got": got},
        ))


def _check_tls_pin(
    view: DocView, expected: ExpectedClaims, result: VerificationResult
) -> None:
    cert_der: Optional[bytes] = expected.served_cert_der
    if cert_der is None:
        # No cert supplied for offline verification. build_pinned_client performs the live
        # check at connection time; here we record that the live pin is deferred.
        result.add(CheckResult(
            "tls_pin", True,
            "no served cert supplied for offline check — pin is enforced live by "
            "build_pinned_client at connection time",
            required=False,
            detail={"tls_cert_fingerprint": view.tls_fpr},
        ))
        return
    try:
        vk = view.verify_key_bytes
    except MalformedDocument as exc:
        result.add(CheckResult("tls_pin", False, f"cannot read enclave verify key: {exc}"))
        return
    ok, reason = verify_tls_pin(
        cert_der,
        view.tls_fpr,
        view.tls_pubkey_signature or "",
        vk,
    )
    result.add(CheckResult("tls_pin", ok, reason,
                           detail={"tls_cert_fingerprint": view.tls_fpr}))
