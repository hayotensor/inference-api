"""ProvisionerService: attest each model-enclave of a miner, seal creds into it.

A miner advertises N single-model enclaves (``MinerModel`` rows). ``provision_miner``
loops over them and, for EACH model-enclave (``_provision_model``):
  1. fresh random nonce -> AsyncTeeClient.get_attestation(nonce)
  2. verify the document via ``talaris_attest.verify_attestation`` with an
     ExpectedClaims whose ``expected_miner_hash`` is the miner hotkey's miner_hash
     and whose ``model_allowlist`` pins the platform-approved artifacts for this model
  3. confirm ``doc.miner == registration_miner_hash`` (registration<->attestation bind)
  4. mint a fresh admin token + inference key(s)
  5. ``talaris_contracts.build_provision_request(Credentials(...), sealing_key, platform_key)``
  6. POST /provision via the TEE client
  7. persist a per-(miner, model) encrypted token + tls_cert_fingerprint +
     enclave_verify_key + model_hash + usage snapshot + expiries on the MinerModel row
     and mark THAT model ``attested``.

After the loop, a miner-level aggregate (status/mode/cert/usage from the "primary"
enclave) is rolled up for back-compat with the provisioner loop, which still selects
candidates by ``Miner.attestation_status``.

NONE of the security-critical logic (attestation verification, provision payload
construction, miner-hash) is reimplemented here — it all goes through the shared
packages.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select

from talaris_attest import (
    ExpectedClaims,
    IntelPolicy,
    ModelAllowlistEntry,
    NvidiaPolicy,
    build_expected_claims as build_expected_claims_factory,
    verify_attestation,
)
from talaris_contracts import (
    AsyncTeeClient,
    Credentials,
    build_provision_request,
    miner_hash as contracts_miner_hash,
)

from inference_api.config import settings
from inference_api.crypto import encrypt_token
from inference_api.models import Miner, MinerModel, ModelAllowlist, ProvisionedToken
from inference_api.provisioner.key import load_provisioner_signing_key
from inference_api.security import expires_in, utcnow

logger = logging.getLogger(__name__)

# How the platform mints credentials it seals into the enclave.
_DEFAULT_INFERENCE_KEY_ID = "platform-default"


class ProvisionError(RuntimeError):
    """A miner could not be provisioned (attestation/transport/verify failure)."""


@dataclass(frozen=True)
class ProvisionOutcome:
    miner_id: uuid.UUID
    key_id: str
    attestation_mode: str
    tls_cert_fingerprint: str | None
    enclave_verify_key: str | None
    reprovisioned: bool


def _allow_dev_attestation() -> bool:
    """Whether the provisioner runs on the insecure dev/stub attestation path.

    Dev when ``allow_dev_attestation`` is set OR the explicit verifier backend
    selector names a non-production stub ("mock"/"dev"). Production otherwise —
    so the real NVIDIA NRAS + Intel PCS backends are used and dev docs are
    rejected (fail closed).
    """
    return bool(settings.allow_dev_attestation) or settings.verifier_backend in {
        "mock",
        "dev",
    }


def _nvidia_root_pem() -> str | None:
    """Resolve the NVIDIA NRAS signing root: inline PEM, or a path we read.

    ``nvidia_root_pem`` may carry the PEM verbatim (contains a BEGIN marker) or a
    filesystem path to it. None keeps the backend default (production then fails
    closed on the missing root, which is correct).
    """
    raw = settings.nvidia_root_pem
    if not raw:
        return None
    if "BEGIN" in raw:
        return raw
    return Path(raw).read_text()


def build_expected_claims(
    *,
    expected_miner_hash: str,
    model_allowlist: list[ModelAllowlistEntry] | None = None,
) -> ExpectedClaims:
    """Construct the verifier policy from config via the SHARED factory.

    All policy construction (dev vs production, which real backends run, whether
    insecure dev is permitted) is delegated to
    ``talaris_attest.build_expected_claims`` so the inference-api provisioner
    cannot diverge from the other verifying plane. In dev
    (``allow_dev_attestation`` / a stub ``verifier_backend``) the factory selects
    the insecure stub path; otherwise it builds the REAL NvidiaNras + IntelPcs
    backends from settings (None settings keep the backends' production-safe
    defaults).
    """
    return build_expected_claims_factory(
        allow_dev=_allow_dev_attestation(),
        expected_miner_hash=expected_miner_hash,
        model_allowlist=model_allowlist,
        nvidia=NvidiaPolicy(
            jwks_url=settings.nras_jwks_url,
            expected_issuer=settings.nras_expected_issuer,
            nvidia_root_pem=_nvidia_root_pem(),
            nras_url=settings.nras_url,
        ),
        intel=IntelPolicy(
            allowed_tcb_statuses=settings.intel_allowed_tcb_statuses,
            require_collateral=settings.intel_require_collateral,
        ),
        require_gpu_evidence=settings.require_gpu_evidence,
        require_tdx_quote=settings.require_tdx_quote,
    )


class ProvisionerService:
    """Owns the provision lifecycle for a single miner across its model-enclaves.

    Each advertised ``MinerModel`` is its own single-model enclave reachable at
    ``MinerModel.tee_endpoint`` (falling back to ``Miner.tee_endpoint`` when null,
    e.g. a legacy single-model miner). Provisioning attests + seals a fresh
    credential into EACH model-enclave and stamps per-model attestation/usage
    state, then rolls a miner-level aggregate up for back-compat with the
    provisioner loop (which selects candidates by ``Miner.attestation_status``).

    An ``AsyncTeeClient`` factory may be injected (tests inject one wrapping the
    in-process dev-TEE TestClient transport); the factory is called per enclave as
    ``factory(endpoint: str) -> AsyncTeeClient``. The default builds an unpinned
    client against the resolved enclave endpoint.
    """

    def __init__(
        self,
        session,
        *,
        tee_client_factory=None,
    ) -> None:
        self.session = session
        # factory(endpoint) -> AsyncTeeClient ; default builds an unpinned client.
        self._tee_client_factory = tee_client_factory or self._default_client_factory

    def _default_client_factory(self, endpoint: str) -> AsyncTeeClient:
        timeout = httpx.Timeout(
            settings.tee_provision_timeout_seconds,
            connect=settings.tee_connect_timeout_seconds,
        )
        return AsyncTeeClient(base_url=endpoint, timeout=timeout)

    async def provision_miner(self, miner_id: uuid.UUID) -> ProvisionOutcome:
        miner = await self.session.get(Miner, miner_id)
        if miner is None:
            raise ProvisionError(f"miner {miner_id} not found")
        if not miner.hotkey:
            raise ProvisionError("miner has no hotkey")

        expected_hash = contracts_miner_hash(miner.hotkey)
        miner.attestation_status = "verifying"
        self.session.add(miner)
        await self.session.flush()

        try:
            return await self._run(miner, expected_hash)
        except ProvisionError:
            miner.attestation_status = "failed"
            self.session.add(miner)
            await self.session.flush()
            raise
        except (httpx.HTTPError, ValueError) as exc:
            miner.attestation_status = "failed"
            self.session.add(miner)
            await self.session.flush()
            raise ProvisionError(str(exc)) from exc

    async def _run(self, miner: Miner, expected_hash: str) -> ProvisionOutcome:
        """Provision EACH advertised model-enclave, then roll up the miner aggregate.

        A per-model failure is isolated (logged, the model left non-attested) so a
        single bad enclave doesn't sink the whole miner. The miner is marked
        ``attested`` if at least one model provisioned; if ALL models fail (or there
        are none) a ``ProvisionError`` is raised so the caller stamps ``failed``.
        """
        models = await self._advertised_models(miner.id)
        if not models:
            raise ProvisionError("miner has no advertised models")

        successes: list[tuple[MinerModel, ProvisionOutcome]] = []
        failures: list[tuple[MinerModel, Exception]] = []
        for mm in models:
            endpoint = mm.tee_endpoint or miner.tee_endpoint
            client = self._tee_client_factory(endpoint)
            try:
                outcome = await self._provision_model(
                    miner, mm, client, expected_hash, endpoint
                )
                successes.append((mm, outcome))
            except (ProvisionError, httpx.HTTPError, ValueError) as exc:
                failures.append((mm, exc))
                logger.warning(
                    "provisioner.model_failed",
                    extra={
                        "miner_id": str(miner.id),
                        "model": mm.model_id,
                        "error": str(exc),
                    },
                )
            finally:
                await client.aclose()

        if not successes:
            reasons = "; ".join(f"{mm.model_id}: {exc}" for mm, exc in failures)
            raise ProvisionError(
                f"all advertised models failed to provision: {reasons}"
            )

        # Miner-level aggregate (back-compat): mirror the "primary" enclave —
        # the one served at the miner's own tee_endpoint (legacy single-model
        # fallback), else the first model that provisioned successfully.
        primary_mm, primary_outcome = successes[0]
        for mm, outcome in successes:
            if (mm.tee_endpoint or miner.tee_endpoint) == miner.tee_endpoint:
                primary_mm, primary_outcome = mm, outcome
                break

        miner.attestation_status = "attested"
        miner.attestation_mode = primary_mm.attestation_mode
        miner.attestation_verified_at = primary_mm.attestation_verified_at
        miner.attestation_expiry = primary_mm.attestation_expiry
        miner.tls_cert_fingerprint = primary_mm.tls_cert_fingerprint
        miner.enclave_verify_key = primary_mm.enclave_verify_key
        miner.usage_chain_head = primary_mm.usage_chain_head or miner.usage_chain_head
        miner.usage_count = primary_mm.usage_count
        miner.usage_total_tokens = primary_mm.usage_total_tokens
        self.session.add(miner)
        await self.session.flush()

        return primary_outcome

    async def _provision_model(
        self,
        miner: Miner,
        mm: MinerModel,
        client: AsyncTeeClient,
        expected_hash: str,
        endpoint: str,
    ) -> ProvisionOutcome:
        """Attest + seal a credential into ONE model-enclave and stamp its row."""
        # 1. fresh nonce -> attestation
        nonce = secrets.token_bytes(32)
        try:
            doc = await client.get_attestation(nonce)
        except httpx.HTTPError as exc:
            raise ProvisionError(f"attestation fetch failed: {exc}") from exc

        # 2. verify via the shared verifier (single source of truth), pinning the
        #    platform model allowlist for THIS model_id (None -> check skipped).
        allowlist = await self._model_allowlist(mm.model_id)
        result = verify_attestation(
            doc,
            build_expected_claims(
                expected_miner_hash=expected_hash, model_allowlist=allowlist
            ),
            nonce,
        )
        if not result.ok:
            reasons = "; ".join(c.name for c in result.failures)
            raise ProvisionError(f"attestation verification failed: {reasons}")

        # 3. confirm the attested miner matches the registration (defense in depth).
        # doc.miner is None until the enclave is bound; require it to equal the
        # expected hotkey miner_hash.
        if doc.miner is not None and doc.miner != expected_hash:
            raise ProvisionError("attested miner does not match registration hotkey")

        sealing_key = doc.public_bundle.sealing_key
        if not sealing_key:
            raise ProvisionError("attestation document has no enclave sealing key")

        enclave_verify_key = doc.public_bundle.verify_key
        reprovisioned = (
            mm.enclave_verify_key is not None
            and mm.enclave_verify_key != enclave_verify_key
        )

        # 4. mint fresh credentials.
        admin_token = f"adm_{secrets.token_urlsafe(32)}"
        inference_token = f"inf_{secrets.token_urlsafe(32)}"
        key_id = _DEFAULT_INFERENCE_KEY_ID
        creds = Credentials(
            admin_token=admin_token,
            inference_keys={key_id: inference_token},
        )

        # 5. build the provision request via the shared helper (seals + signs).
        platform_key = load_provisioner_signing_key()
        provision_request = build_provision_request(creds, sealing_key, platform_key)

        # 6. POST /provision (prebuilt request is sent verbatim).
        try:
            resp = await client.provision(provision_request)
        except httpx.HTTPError as exc:
            raise ProvisionError(f"provision request failed: {exc}") from exc
        if not resp.get("provisioned"):
            raise ProvisionError(f"enclave rejected provision: {resp!r}")

        # 7. persist — supersede the prior active token for THIS (miner, model) only.
        await self._supersede_active_tokens(miner.id, mm.model_id)
        token_row = ProvisionedToken(
            id=uuid.uuid4(),
            miner_id=miner.id,
            model_id=mm.model_id,
            key_id=key_id,
            encrypted_token=encrypt_token(inference_token),
            admin_encrypted_token=encrypt_token(admin_token),
            status="active",
            provisioned_at=utcnow(),
            expires_at=expires_in(seconds=settings.token_ttl_seconds),
        )
        self.session.add(token_row)

        mm.attestation_status = "attested"
        mm.attestation_mode = doc.mode
        mm.attestation_verified_at = utcnow()
        mm.attestation_expiry = expires_in(seconds=settings.attestation_ttl_seconds)
        mm.tls_cert_fingerprint = doc.tls_cert_fingerprint
        mm.enclave_verify_key = enclave_verify_key
        mm.model_hash = doc.model_hash or mm.model_hash
        # Usage rollback-detection snapshot (best-effort from the document).
        mm.usage_chain_head = doc.usage_chain_head or mm.usage_chain_head
        mm.usage_count = doc.usage_count
        mm.usage_total_tokens = doc.usage_total_tokens
        self.session.add(mm)
        await self.session.flush()

        # 8. (optional, flag-gated) the platform holds the only admin token, so only
        # it can drive this enclave's engine. When enabled, start the model in the
        # enclave and record the resulting model_hash. Default-off keeps the legacy
        # provision-only path.
        if settings.provisioner_start_engine:
            await self._maybe_start_engine(miner, mm, admin_token, endpoint)

        return ProvisionOutcome(
            miner_id=miner.id,
            key_id=key_id,
            attestation_mode=doc.mode,
            tls_cert_fingerprint=doc.tls_cert_fingerprint,
            enclave_verify_key=enclave_verify_key,
            reprovisioned=reprovisioned,
        )

    async def _advertised_models(self, miner_id: uuid.UUID) -> list[MinerModel]:
        result = await self.session.execute(
            select(MinerModel).where(MinerModel.miner_id == miner_id)
        )
        return list(result.scalars())

    async def _model_allowlist(
        self, model_id: str
    ) -> list[ModelAllowlistEntry] | None:
        """Read the ACTIVE platform allowlist for ``model_id`` as verifier entries.

        Returns ``None`` when no active rows exist so the verifier SKIPS the
        model-hash check (dev seeds none and the dev TEE reports model_hash=None);
        production with seeded rows enforces the pin via ``build_expected_claims``.
        """
        result = await self.session.execute(
            select(ModelAllowlist).where(
                ModelAllowlist.model_id == model_id,
                ModelAllowlist.active.is_(True),
            )
        )
        entries = [
            ModelAllowlistEntry(
                model_hash=row.model_hash,
                args_hash=row.args_hash,
                gpu_hash=row.gpu_hash,
                label=row.label,
            )
            for row in result.scalars()
        ]
        return entries or None

    async def _maybe_start_engine(
        self, miner: Miner, target: MinerModel, admin_token: str, endpoint: str
    ) -> None:
        """Drive the model-enclave's POST /engine/start for this advertised model.

        Flag-gated (``provisioner_start_engine``). The platform holds the only admin
        token, so it is the sole party that can launch the enclave engine. We POST
        /engine/start with the freshly minted admin token, wait until the engine is
        ready (GET /engine/status, falling back to /attestation showing model_hash),
        then re-attest to capture the loaded model_hash and stamp it onto the
        miner_model (``loaded=True``). Best-effort: a failure here is logged and the
        model stays attested (provisioning already succeeded) — it just won't route
        until a later loop pass retries.

        /engine/* is not part of the shared AsyncTeeClient surface, so we issue raw
        httpx calls against this enclave's ``endpoint`` (unpinned plain HTTP in dev,
        exactly like the forwarder when tls_pin_enforce is off).
        """
        model_id = target.model_id

        timeout = httpx.Timeout(
            settings.provisioner_engine_start_timeout_seconds,
            connect=settings.tee_connect_timeout_seconds,
        )
        headers = {"Authorization": f"Bearer {admin_token}"}
        start_body = {
            "engine": settings.provisioner_engine_name,
            "model": model_id,
        }
        try:
            async with httpx.AsyncClient(
                base_url=endpoint, timeout=timeout, headers=headers
            ) as http:
                # If the engine is already ready (idempotent re-provision), skip start.
                already_ready = await self._engine_ready(http)
                if not already_ready:
                    resp = await http.post("/engine/start", json=start_body)
                    if resp.status_code == 409:
                        # Engine already running for this enclave — treat as ready.
                        pass
                    elif resp.status_code >= 400:
                        logger.warning(
                            "provisioner.engine_start_rejected",
                            extra={
                                "miner_id": str(miner.id),
                                "status": resp.status_code,
                                "body": resp.text[:300],
                            },
                        )
                        return
                    if not await self._await_engine_ready(http):
                        logger.warning(
                            "provisioner.engine_start_timeout",
                            extra={"miner_id": str(miner.id), "model": model_id},
                        )
                        return

                # Re-attest to capture the model_hash the enclave now serves (None in
                # dev/mock mode, a digest in real mode). model_id stays authoritative.
                model_hash = await self._attested_model_hash(http)
        except httpx.HTTPError as exc:
            logger.warning(
                "provisioner.engine_start_transport_error",
                extra={"miner_id": str(miner.id), "error": str(exc)},
            )
            return

        target.loaded = True
        target.status = "loaded"
        if model_hash is not None:
            target.model_hash = model_hash
        target.last_advertised_at = utcnow()
        self.session.add(target)
        await self.session.flush()
        logger.info(
            "provisioner.engine_started",
            extra={
                "miner_id": str(miner.id),
                "model": model_id,
                "model_hash": model_hash,
            },
        )

    async def _engine_ready(self, http: httpx.AsyncClient) -> bool:
        try:
            resp = await http.get("/engine/status")
        except httpx.HTTPError:
            return False
        if resp.status_code != 200:
            return False
        try:
            return resp.json().get("state") == "ready"
        except ValueError:
            return False

    async def _await_engine_ready(self, http: httpx.AsyncClient) -> bool:
        deadline = (
            time.monotonic() + settings.provisioner_engine_start_timeout_seconds
        )
        while time.monotonic() < deadline:
            if await self._engine_ready(http):
                return True
            await asyncio.sleep(0.5)
        return False

    async def _attested_model_hash(self, http: httpx.AsyncClient) -> str | None:
        try:
            resp = await http.get("/attestation", params={"nonce": "00" * 32})
            if resp.status_code != 200:
                return None
            return resp.json().get("model_hash")
        except (httpx.HTTPError, ValueError):
            return None

    async def _supersede_active_tokens(
        self, miner_id: uuid.UUID, model_id: str
    ) -> None:
        result = await self.session.execute(
            select(ProvisionedToken).where(
                ProvisionedToken.miner_id == miner_id,
                ProvisionedToken.model_id == model_id,
                ProvisionedToken.status == "active",
            )
        )
        for token in result.scalars():
            token.status = "superseded"
            self.session.add(token)
        await self.session.flush()
