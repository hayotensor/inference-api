"""NVIDIA local-verifier backend — wraps the nv_attestation_sdk LocalGpuVerifier.

Unlike the NRAS backend (which offloads chain verification to NVIDIA's hosted service),
this backend verifies the GPU evidence LOCALLY using NVIDIA's ``nv_attestation_sdk`` (the
``[nvidia-local]`` extra). That SDK is heavy and hardware-tied; it is imported lazily and
only when this backend is actually used, so the core package stays installable without it.

REAL (and shared with NRAS so the two are interchangeable):
  * the nonce-binding contract: local verification binds the SDK's nonce to the enclave
    ``report_data`` hex (the same value nvattest signed over) and re-checks the EAT's
    echoed nonce equals it;
  * the overall-result + measured-GPU-UUID extraction contract (reused from nvidia_nras).

GUARDED / DOCUMENTED-TODO: the SDK is not installed in this repo / CI, so the import is
guarded and ``verify()`` fails CLOSED with a clear reason naming the missing extra. The
real flow (construct ``Attestation``, ``set_nonce``, ``add_verifier(GPU, LOCAL)``,
``attest(evidence)``, ``get_token()``, then enforce nonce-binding + extract UUIDs) is
implemented against the documented SDK API but only runs when the extra and real evidence
are present.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from .base import BackendResult
from .nvidia_nras import (
    _extract_gpu_uuids,
    _extract_nonce_claim,
    decode_eat_claims,
)


class _SdkUnavailable(RuntimeError):
    pass


# An injectable factory so tests can exercise the verification logic with a fake SDK
# client. Production leaves it None and the real nv_attestation_sdk is imported lazily.
SdkClientFactory = Callable[[], object]


def _import_sdk():
    """Lazily import nv_attestation_sdk; raise a clear error if the extra is missing."""
    try:
        from nv_attestation_sdk import attestation  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise _SdkUnavailable(
            "nv_attestation_sdk is not installed; install the [nvidia-local] extra to "
            "use NvidiaLocalBackend (real local GPU verification needs the SDK + real "
            "NVIDIA GPU evidence)"
        ) from exc
    return attestation


class NvidiaLocalBackend:
    """Local NVIDIA GPU-evidence verifier over nv_attestation_sdk.

    With the SDK absent (the common case in CI / this repo), verify() returns a clear
    fail-closed result naming the missing extra. When the SDK (or an injected
    ``sdk_client_factory``) is present, the same nonce-binding + UUID-extraction contract
    as NvidiaNrasBackend is enforced, so the two backends are interchangeable.
    """

    is_secure = True

    def __init__(
        self,
        *,
        policy: object = None,
        sdk_client_factory: Optional[SdkClientFactory] = None,
    ) -> None:
        # TODO [nvidia-local]: an appraisal policy (NVIDIA's relying-party policy JSON)
        # the returned token is evaluated against.
        self.policy = policy
        self._sdk_client_factory = sdk_client_factory

    def _build_client(self):
        """Construct the SDK attestation client (or the injected fake)."""
        if self._sdk_client_factory is not None:
            return self._sdk_client_factory()
        attestation = _import_sdk()
        # Documented SDK flow: a named Attestation client with a local GPU verifier.
        client = attestation.Attestation()
        client.set_name("talaris-local-gpu")
        client.set_nonce("")  # set per-request in verify()
        client.add_verifier(
            attestation.Devices.GPU,
            attestation.Environment.LOCAL,
            "",  # URL (unused for LOCAL)
            "",  # policy/OCSP url (unused here)
        )
        return client

    def verify(self, gpu_evidence: object, expected_nonce_hex: str) -> BackendResult:
        try:
            client = self._build_client()
        except _SdkUnavailable as exc:
            return BackendResult(verified=False, reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - SDK construction failure -> fail closed
            return BackendResult(
                verified=False,
                reason=f"NvidiaLocalBackend SDK setup failed: {type(exc).__name__}: {exc}",
            )

        # Bind THIS request's nonce (== enclave report_data hex) into the verifier.
        try:
            client.set_nonce(expected_nonce_hex)
            ok = client.attest(evidence=gpu_evidence)
            token = client.get_token()
        except Exception as exc:  # noqa: BLE001 - any SDK failure must not crash; fail closed
            return BackendResult(
                verified=False,
                reason=f"local GPU attestation raised: {type(exc).__name__}: {exc}",
            )

        if not ok:
            return BackendResult(
                verified=False,
                reason="nv_attestation_sdk local verification returned not-ok (chain/policy)",
            )

        # Optionally evaluate the token against the relying-party policy, when configured.
        if self.policy is not None:
            try:
                policy_ok = client.validate_token(self.policy, token)
            except Exception as exc:  # noqa: BLE001
                return BackendResult(
                    verified=False,
                    reason=f"local token policy evaluation raised: {exc!r}",
                )
            if not policy_ok:
                return BackendResult(
                    verified=False,
                    reason="local token failed relying-party policy evaluation",
                )

        # Enforce the SAME nonce-binding + UUID-extraction contract as NRAS.
        claims = self._token_to_claims(token)
        if claims is None:
            return BackendResult(
                verified=False, reason="local token had no decodable EAT claims"
            )

        echoed_nonce = _extract_nonce_claim(claims)
        if echoed_nonce is None:
            return BackendResult(
                verified=False, reason="local EAT has no nonce claim to bind against"
            )
        if echoed_nonce.lower() != expected_nonce_hex.lower():
            return BackendResult(
                verified=False,
                reason=(
                    "local EAT nonce does not bind this request "
                    f"({echoed_nonce!r} != {expected_nonce_hex!r})"
                ),
            )

        overall = claims.get("x-nvidia-overall-att-result")
        if overall is not None and overall not in (True, "true", "success", "OK"):
            return BackendResult(
                verified=False,
                reason=f"local overall attestation result not success: {overall!r}",
            )

        measured = _extract_gpu_uuids(claims)
        return BackendResult(
            verified=True,
            measured_gpu_uuids=measured,
            measurements={"eat_claims": claims},
            reason="local NVIDIA verification passed; EAT nonce bound to report_data",
        )

    @staticmethod
    def _token_to_claims(token: object) -> Optional[dict]:
        """Normalize an SDK token into a claims dict.

        The SDK's ``get_token()`` returns the relying-party token. Across SDK versions it
        may be a JWT/JWS compact string, a JSON string, or already a dict; handle each.
        """
        if isinstance(token, dict):
            return token
        if isinstance(token, (bytes, bytearray)):
            token = token.decode("utf-8", "replace")
        if isinstance(token, str):
            # Compact JWS (header.payload.signature): decode the payload claims.
            if token.count(".") == 2:
                try:
                    return decode_eat_claims(token)
                except ValueError:
                    pass
            # Otherwise try plain JSON; the SDK sometimes wraps a list ["JWT", {...}].
            try:
                parsed = json.loads(token)
            except (ValueError, TypeError):
                return None
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        return item
                    if isinstance(item, str) and item.count(".") == 2:
                        try:
                            return decode_eat_claims(item)
                        except ValueError:
                            continue
        return None
