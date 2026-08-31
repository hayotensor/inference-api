"""Shared test fixtures.

The producer TEE app writes its self-signed TLS material to disk on boot
(default ``/run/tee``, which is not writable in this dev/CI environment). The
``tee_settings`` fixture builds a producer ``Settings`` pointed at a writable
temp dir so the FastAPI ``TestClient`` lifespan can boot.
"""

from __future__ import annotations

import pytest

# Only define the producer-backed fixtures if tee_wrapper is importable
# (PYTHONPATH=.../inference-subnet-miner/src). Pure-mirror tests don't need them.
tee_available = True
try:  # pragma: no cover - environment gating
    from tee_wrapper.config import Settings  # type: ignore
except Exception:  # noqa: BLE001
    tee_available = False


if tee_available:
    from nacl.signing import SigningKey  # noqa: E402

    @pytest.fixture
    def tee_settings(tmp_path):
        """Producer Settings for a bootable dev app, with writable TLS paths.

        Returns a factory so a test can override fields (e.g. validator keys).
        """

        def _make(**overrides):
            vk_hex = bytes(SigningKey.generate().verify_key).hex()
            base = dict(
                dev_mode=True,
                usage_db_path=":memory:",
                attestation_mode="dev",
                # This box can be detected as CC hardware; allow the dev stub.
                allow_dev_mode=True,
                validator_static_keys=vk_hex,
                tls_cert_path=str(tmp_path / "cert.pem"),
                tls_key_path=str(tmp_path / "key.pem"),
            )
            base.update(overrides)
            return Settings(**base)

        return _make
