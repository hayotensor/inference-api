"""Caller-supplied policy for what a *valid* attestation must claim.

ExpectedClaims is the verifier's policy object. Its DEFAULTS are PRODUCTION-SAFE:
  * required_modes == {"nvidia"}  -> only hardware-attested docs pass the mode gate.
  * allow_insecure_dev == False   -> dev docs are rejected, and the stub backends are
                                     refused (the second, independent dev gate).
  * require_gpu_evidence / require_tdx_quote == True -> hardware evidence is mandatory.

A test/dev caller must OPT IN to the insecure path by setting allow_insecure_dev=True
(and typically required_modes={"dev"} and the stub backends).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid import cycle at runtime
    from .backends.base import GpuEvidenceBackend, TdxQuoteBackend


@dataclass(frozen=True)
class ModelAllowlistEntry:
    """One allowed (model, [args], [gpu]) tuple.

    model_hash:  REQUIRED — must equal doc.model_hash for this entry to match.
    args_hash:   optional pin — when set, doc.args_hash must also equal it.
    gpu_hash:    optional pin — when set, doc.gpu_hash must also equal it.
    label:       optional human label for diagnostics.
    """

    model_hash: str
    args_hash: Optional[str] = None
    gpu_hash: Optional[str] = None
    label: Optional[str] = None

    def matches(self, model_hash: Optional[str], args_hash: Optional[str],
                gpu_hash: Optional[str]) -> bool:
        if model_hash is None or model_hash != self.model_hash:
            return False
        if self.args_hash is not None and args_hash != self.args_hash:
            return False
        if self.gpu_hash is not None and gpu_hash != self.gpu_hash:
            return False
        return True


_MEASUREMENT_FIELDS = ("mrtd", "rtmr0", "rtmr1", "rtmr2", "rtmr3")
_MEASUREMENT_HEX_LEN = 96  # 48-byte SHA-384 registers


def _norm_measurement(value: object, name: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != _MEASUREMENT_HEX_LEN:
        raise ValueError(
            f"{name} must be {_MEASUREMENT_HEX_LEN} hex chars (48 bytes), got {len(text)}"
        )
    try:
        bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError(f"{name} is not valid hex") from exc
    return text


@dataclass(frozen=True)
class TdxMeasurementEntry:
    """One allowed Intel TDX software measurement (a published enclave build).

    The TDX quote carries the TD's software measurements: ``mrtd`` (the initial TD
    memory / virtual firmware), ``rtmr0`` (firmware config + ACPI), ``rtmr1`` (kernel /
    boot loader), ``rtmr2`` (kernel cmdline + initrd, which is where a dm-verity root
    hash for the enclave image lives) and ``rtmr3`` (runtime / application-defined).
    Without a pin, ANY software running in a genuine TD on genuine hardware passes.

    mrtd:            REQUIRED.
    rtmr0..rtmr3:    optional pins — when set, the quoted register must equal it.
                     Pin every register your build reproduces; leave a register unset
                     only when it legitimately varies across hosts (rtmr0 can vary with
                     the VMM's ACPI tables / vCPU count).
    label:           optional human label (e.g. "talaris-tee v1.4.0").

    Values are lowercased 96-hex-char strings (an optional ``0x`` is stripped).
    """

    mrtd: str
    rtmr0: Optional[str] = None
    rtmr1: Optional[str] = None
    rtmr2: Optional[str] = None
    rtmr3: Optional[str] = None
    label: Optional[str] = None

    def __post_init__(self) -> None:
        for name in _MEASUREMENT_FIELDS:
            value = getattr(self, name)
            if name == "mrtd" and value is None:
                raise ValueError("mrtd is required in a TDX measurement allowlist entry")
            object.__setattr__(self, name, _norm_measurement(value, name))

    def matches(self, measurements: Optional[dict]) -> bool:
        if not isinstance(measurements, dict):
            return False
        for name in _MEASUREMENT_FIELDS:
            want = getattr(self, name)
            if want is None:
                continue
            got = measurements.get(name)
            if not isinstance(got, str) or got.lower() != want:
                return False
        return True


def load_measurement_allowlist(spec: object) -> Optional[tuple[TdxMeasurementEntry, ...]]:
    """Parse a TDX measurement allowlist from config.

    Accepts ``None``/``""`` (not configured -> ``None``), an iterable of entries or
    dicts, a JSON string (a list of ``{"mrtd": ..., "rtmr0": ..., "label": ...}``
    objects, or one such object), or ``@/path/to/file.json`` / a path to an existing
    ``.json`` file holding that JSON. Raises ``ValueError`` on malformed input so a
    typo in production config fails loudly at startup instead of silently disabling
    the pin.
    """
    import json
    import os

    if spec is None:
        return None
    if isinstance(spec, TdxMeasurementEntry):
        return (spec,)
    if isinstance(spec, str):
        text = spec.strip()
        if not text:
            return None
        if text.startswith("@") or (text.endswith(".json") and os.path.isfile(text)):
            path = text[1:] if text.startswith("@") else text
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"TDX measurement allowlist is not valid JSON: {exc}") from exc
    if isinstance(spec, dict):
        spec = [spec]
    entries: list[TdxMeasurementEntry] = []
    try:
        items = list(spec)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("TDX measurement allowlist must be a list of entries") from exc
    for item in items:
        if isinstance(item, TdxMeasurementEntry):
            entries.append(item)
        elif isinstance(item, dict):
            unknown = set(item) - set(_MEASUREMENT_FIELDS) - {"label"}
            if unknown:
                raise ValueError(f"unknown TDX measurement field(s): {sorted(unknown)}")
            if not item.get("mrtd"):
                raise ValueError("every TDX measurement allowlist entry needs an mrtd")
            entries.append(TdxMeasurementEntry(**item))
        else:
            raise ValueError(f"invalid TDX measurement allowlist entry: {item!r}")
    if not entries:
        raise ValueError(
            "TDX measurement allowlist is configured but empty (unset it to disable)"
        )
    return tuple(entries)


@dataclass(frozen=True)
class FreshnessPolicy:
    """Optional freshness / monotonicity policy.

    The nonce echo (step 2) is the primary freshness control. This policy carries
    optional monotonic high-water marks a long-lived validator tracks across polls
    (usage_count / usage_total_tokens never regress; usage_chain_head advances). When a
    bound is None the corresponding check is skipped. These are advisory and recorded as
    informational checks unless ``required`` is set.
    """

    min_usage_count: Optional[int] = None
    min_usage_total_tokens: Optional[int] = None
    required: bool = False


@dataclass
class ExpectedClaims:
    """Caller policy. Defaults are production-safe (see module docstring)."""

    # --- mode gate (first dev gate) ---------------------------------------
    required_modes: frozenset[str] = frozenset({"nvidia"})
    allow_insecure_dev: bool = False

    # --- hardware-evidence requirements -----------------------------------
    require_gpu_evidence: bool = True
    require_tdx_quote: bool = True

    # --- pluggable backends -----------------------------------------------
    # When None, a default is chosen by verify(): production -> real backends
    # (nvidia_nras / intel_pcs); insecure-dev -> stub backends. A stub backend is
    # ALWAYS refused in production via the backend_insecure_gate check.
    nvidia_backend: "Optional[GpuEvidenceBackend]" = None
    tdx_backend: "Optional[TdxQuoteBackend]" = None

    # --- allowlists / expected values -------------------------------------
    model_allowlist: Optional[list[ModelAllowlistEntry]] = None
    expected_miner_hash: Optional[str] = None

    # --- enclave software measurement pin (Intel TDX MRTD / RTMRs) ---------
    # When set, the TDX-VERIFIED measurements must match at least one entry (a
    # published enclave build); a document without a verified quote then fails
    # closed. When unset, the check is informational unless
    # ``require_measurement_pin`` is True, in which case "no pin configured" itself
    # fails — the production opt-in that guarantees a deployment cannot silently run
    # without a software pin.
    tdx_measurement_allowlist: Optional[tuple[TdxMeasurementEntry, ...]] = None
    require_measurement_pin: bool = False

    # --- freshness --------------------------------------------------------
    freshness: FreshnessPolicy = field(default_factory=FreshnessPolicy)

    # --- TLS pin ----------------------------------------------------------
    # Optional cert DER supplied by an offline caller so the TLS-pin step can run without
    # a live connection. build_pinned_client checks the served cert live instead.
    served_cert_der: Optional[bytes] = None

    def is_insecure_dev(self) -> bool:
        return bool(self.allow_insecure_dev)
