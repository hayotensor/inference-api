"""talaris-e2ee/v1 relay + billing: ``/v1/e2e/*``.

The client encrypts an OpenAI body to ONE attested enclave's HPKE key; this plane
routes and bills from the plaintext metadata headers only and never sees the
prompt or the answer. Wire format, header names, error codes and usage parsing all
come from ``talaris_contracts.e2ee`` (spec: talaris-e2ee/docs/PROTOCOL.md) --
nothing about the protocol is re-implemented here.

Everything that is not protocol is deliberately shared with the plaintext
``/v1/chat/completions`` path rather than forked:

* auth = ``require_api_key(["inference:write"])`` (key validity, owner, IP
  allowlist, rate limit) + ``product_routes._check_model_allowed``;
* eligibility = ``MinerSelector`` (``select(..., e2ee_only=True)`` /
  ``select_instance``), the same attested+loaded+healthy+active-token predicate;
* dialing = ``TEEForwarder`` (token lookup, TLS-leaf-pinned client honoring
  ``tls_pin_enforce``, pin-mismatch -> miner ``stale``);
* billing = ``UsageService`` reserve/settle/release, stats = ``stats_recorder``,
  and streaming settlement in a ``finally`` with a fresh session, applying the
  HYP-56 disconnect rule exactly as ``_routed_stream_response`` does.

Never log request or response bodies here: they are ciphertext, but their sizes
and timing are already the only things this plane is supposed to learn.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts.e2ee import (
    H_ENCRYPTED,
    H_ERROR,
    H_INSTANCE,
    H_MAX_TOKENS,
    H_MODEL,
    H_NONCE,
    H_PATH,
    H_RETRY,
    H_STREAM,
    H_USAGE,
    H_VERSION,
    NONCE_LEN,
    E2EEError,
    RequestMeta,
    key_id,
)

from inference_api import stats_recorder
from inference_api.api_keys import APIKeyService, require_api_key
from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import service_unavailable, unauthorized
from inference_api.logging import request_id_ctx
from inference_api.models import APIKey, InferenceUsageEvent, User
from inference_api.product_routes import (
    _attestation_receipt_for,
    _check_model_allowed,
    _is_model_registered,
    _make_forwarder,
    _model_not_found,
)
from inference_api.routing.forwarder import (
    OUTCOME_HTTP_ERROR,
    OUTCOME_PIN_MISMATCH,
    OUTCOME_SUCCESS,
    EnclaveExchange,
    ForwardError,
    TEEForwarder,
    usage_from_e2ee,
)
from inference_api.routing.selector import MinerCandidate, MinerSelector
from inference_api.schemas import OpenAIChatCompletionRequest
from inference_api.usage import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/e2e", tags=["e2ee"])

MAX_INSTANCES = 5
MAX_NONCES_PER_INSTANCE = 16
_NONCE_RE = re.compile(r"^[0-9a-fA-F]+$")
# Response headers relayed from the enclave on a protocol error / sealed response.
_E2EE_ERROR_HEADERS = (H_ERROR, H_RETRY)
H_RECEIPT = "X-Talaris-Receipt"
# The id this plane billed + recorded stats under (== the X-Request-Id forwarded to
# the enclave). Normally the caller's X-Request-Id; see _billing_request_id.
H_BILLING_REQUEST_ID = "X-Talaris-Request-Id"

# For CORS (main.py): what a browser client sends and must be able to read back.
E2EE_REQUEST_HEADERS = (H_VERSION, H_NONCE, H_PATH, H_MODEL, H_STREAM, H_MAX_TOKENS, H_INSTANCE)
E2EE_RESPONSE_HEADERS = (H_ENCRYPTED, H_ERROR, H_RETRY, H_USAGE, H_RECEIPT, H_BILLING_REQUEST_ID)


def _plaintext_max_tokens_cap() -> int:
    """The plaintext path's max_tokens ceiling, read off its own schema so the two
    paths can never drift apart."""
    for meta in OpenAIChatCompletionRequest.model_fields["max_tokens"].metadata:
        le = getattr(meta, "le", None)
        if le is not None:
            return int(le)
    raise RuntimeError("OpenAIChatCompletionRequest.max_tokens has no upper bound")


MAX_TOKENS_CAP = _plaintext_max_tokens_cap()


def _e2ee_error(code: str, message: str = "") -> JSONResponse:
    err = E2EEError(code, message)
    return JSONResponse(err.to_body(), status_code=err.status, headers=err.to_headers())


def _parse_instance_id(raw: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


async def _eligible_e2ee_instance(session: AsyncSession, raw_id: str | None) -> MinerCandidate | None:
    """The instance iff currently eligible (same predicate as selection) AND it has
    an attested E2EE key."""
    instance_id = _parse_instance_id(raw_id)
    if instance_id is None:
        return None
    candidate = await MinerSelector(session).select_instance(instance_id)
    if candidate is None or not candidate.e2ee_public_key:
        return None
    return candidate


# --------------------------------------------------------------------------- #
# GET /v1/e2e/instances
# --------------------------------------------------------------------------- #


def _valid_nonces(raw, count: int) -> list[str] | None:
    if not isinstance(raw, list) or not raw or len(raw) > count:
        return None
    out = []
    for nonce in raw:
        if not isinstance(nonce, str) or len(nonce) != 2 * NONCE_LEN or not _NONCE_RE.match(nonce):
            return None
        out.append(nonce)
    return out


@router.get("/instances")
async def list_e2ee_instances(
    model: str = Query(..., min_length=1, max_length=256),
    limit: int = Query(3, ge=1, le=MAX_INSTANCES),
    nonces: int = Query(4, ge=1, le=MAX_NONCES_PER_INSTANCE),
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["inference:write"])),
):
    """Up to ``limit`` E2EE-capable enclaves serving ``model``, each with fresh,
    enclave-issued single-use nonces. The client must verify each key itself (via
    ``/instances/{id}/attestation``); this listing is a routing hint, not a vouch.
    """
    _check_model_allowed(db_key, model)
    candidates = await MinerSelector(session).select(model, e2ee_only=True)
    if not candidates:
        if not await _is_model_registered(session, model):
            raise _model_not_found(model)
        raise service_unavailable("no_capacity", f"No E2EE-capable attested miner is serving model {model!r}")

    forwarder = _make_forwarder(session)
    # Over-dial a little so a couple of dead enclaves don't shrink the answer.
    pool = candidates[: limit * 2]
    # Token lookups use the session, so they run sequentially BEFORE the
    # concurrent dials (an AsyncSession must not be shared across tasks).
    bearers: dict[object, str] = {}
    for candidate in pool:
        try:
            bearers[candidate.miner_model_id] = await forwarder.active_bearer(candidate)
        except ForwardError:
            continue
    dialable = [c for c in pool if c.miner_model_id in bearers]

    semaphore = asyncio.Semaphore(settings.e2ee_nonce_fetch_concurrency)

    async def fetch(candidate: MinerCandidate):
        async with semaphore:
            try:
                return await forwarder.e2ee_nonces(
                    candidate,
                    bearers[candidate.miner_model_id],
                    nonces,
                    timeout=settings.e2ee_nonce_fetch_timeout_seconds,
                )
            except ForwardError as exc:
                return exc

    results = await asyncio.gather(*(fetch(c) for c in dialable))

    stale_miners: set = set()
    instances: list[dict] = []
    for candidate, result in zip(dialable, results, strict=True):
        if isinstance(result, ForwardError):
            if result.outcome == OUTCOME_PIN_MISMATCH:
                stale_miners.add(candidate.miner_id)
            logger.info(
                "e2ee.nonces_failed",
                extra={"miner_model_id": str(candidate.miner_model_id), "outcome": result.outcome},
            )
            continue
        if result.get("e2ee_key") != candidate.e2ee_public_key:
            # The enclave is serving a key other than the one attested at
            # provisioning (rebooted, or something is substituting keys): treat it
            # exactly like a TLS pin mismatch -- stale until re-provisioned.
            stale_miners.add(candidate.miner_id)
            logger.warning(
                "e2ee.key_mismatch",
                extra={"miner_id": str(candidate.miner_id), "miner_model_id": str(candidate.miner_model_id)},
            )
            continue
        valid = _valid_nonces(result.get("nonces"), nonces)
        if valid is None:
            continue
        expires_in = result.get("expires_in")
        instances.append(
            {
                "instance_id": str(candidate.miner_model_id),
                "e2ee_key": candidate.e2ee_public_key,
                # Computed from the ATTESTED key, not taken from the enclave reply.
                "key_id": key_id(bytes.fromhex(candidate.e2ee_public_key)),
                "nonces": valid,
                "nonce_expires_in": expires_in if isinstance(expires_in, int) and expires_in > 0 else 60,
                "attestation_mode": candidate.attestation_mode,
                "model_hash": candidate.model_hash,
            }
        )

    for miner_id in stale_miners:
        await forwarder.mark_stale(miner_id)
    # Deliberately no APIKeyService.record_usage row: clients list before every
    # invoke, and those rows feed /v1/usage's "requests" counter (inference only).
    await session.commit()
    if not instances:
        raise service_unavailable("no_capacity", f"No E2EE-capable miner for model {model!r} answered")
    return {"object": "list", "model": model, "instances": instances[:limit]}


# --------------------------------------------------------------------------- #
# GET /v1/e2e/instances/{instance_id}/attestation
# --------------------------------------------------------------------------- #


@router.get("/instances/{instance_id}/attestation")
async def get_e2ee_instance_attestation(
    instance_id: str,
    nonce: str = Query(...),
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["inference:write"])),
):
    """Pass the enclave's raw ``/attestation`` document through, verbatim.

    The client verifies it (fresh nonce, report_data, identity_binding over the
    E2EE key, hardware evidence); the platform adds nothing it would have to trust.
    """
    # Validate before dialing anything: hex, 16..64 bytes.
    if not (32 <= len(nonce) <= 128 and len(nonce) % 2 == 0 and _NONCE_RE.match(nonce)):
        return _e2ee_error("bad_metadata", "nonce must be 16-64 bytes of hex")
    candidate = await _eligible_e2ee_instance(session, instance_id)
    if candidate is None:
        return _e2ee_error("instance_unavailable", "instance is not currently available")
    _check_model_allowed(db_key, candidate.model_id)
    forwarder = _make_forwarder(session)
    try:
        raw = await forwarder.e2ee_attestation(candidate, nonce)
    except ForwardError as exc:
        if exc.outcome == OUTCOME_PIN_MISMATCH:
            await forwarder.mark_stale(candidate.miner_id)
            await session.commit()
        return _e2ee_error("instance_unavailable", "instance attestation could not be fetched")
    return Response(content=raw, media_type="application/json")


# --------------------------------------------------------------------------- #
# POST /v1/e2e/invoke
# --------------------------------------------------------------------------- #


async def _read_body_capped(request: Request, cap: int) -> bytes | None:
    """The raw request body, or None once it exceeds ``cap`` (never buffers more)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                return None
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _billing_request_id(session: AsyncSession, user: User) -> str:
    """The id to reserve/forward/record under.

    Normally the caller's X-Request-Id (echoed by the middleware). But
    ``reserve_inference_tokens`` is idempotent on (user, request_id), so a REUSED
    id would hand back the earlier, already released/settled event -- and the
    protocol expects clients to retry (re-encrypting to another instance) after
    ``instance_unavailable``/``nonce_unknown``. A reused id therefore gets a fresh
    server id instead, returned in ``X-Talaris-Request-Id``.
    """
    rid = request_id_ctx.get()
    if rid:
        existing = (
            await session.execute(
                select(InferenceUsageEvent.id).where(
                    InferenceUsageEvent.user_id == user.id,
                    InferenceUsageEvent.request_id == rid,
                )
            )
        ).first()
        if existing is None:
            return rid
    return str(uuid.uuid4())


def _receipt_header(candidate: MinerCandidate, *, request_id: str, model: str) -> dict[str, str]:
    """The platform-signed ``inference-receipt/v1`` (same one /v1/chat/completions
    returns in ``talaris_attestation``) as base64url JSON. Best-effort."""
    try:
        receipt = _attestation_receipt_for(candidate, request_id=request_id, model_id=model)
    except Exception:  # noqa: BLE001 - a receipt must never fail a served response
        logger.warning("e2ee.receipt_failed", exc_info=True)
        return {}
    raw = json.dumps(receipt.model_dump(mode="json"), separators=(",", ":")).encode()
    return {H_RECEIPT: base64.urlsafe_b64encode(raw).decode().rstrip("=")}


@router.post("/invoke")
async def invoke_e2ee(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["inference:write"])),
):
    try:
        meta = RequestMeta.from_headers(request.headers)
    except E2EEError as err:
        return JSONResponse(err.to_body(), status_code=err.status, headers=err.to_headers())
    instance_header = request.headers.get(H_INSTANCE)
    if not instance_header:
        return _e2ee_error("bad_metadata", f"missing header {H_INSTANCE}")
    # Same allowlist enforcement (and ordering: before any capacity signal) as the
    # plaintext path.
    _check_model_allowed(db_key, meta.model)
    if meta.max_tokens > MAX_TOKENS_CAP:
        return _e2ee_error("bad_metadata", f"max_tokens may not exceed {MAX_TOKENS_CAP}")
    body = await _read_body_capped(request, settings.e2ee_max_body_bytes)
    if body is None:
        return _e2ee_error("payload_too_large", f"ciphertext exceeds {settings.e2ee_max_body_bytes} bytes")
    if not body:
        return _e2ee_error("bad_metadata", "empty ciphertext")

    candidate = await _eligible_e2ee_instance(session, instance_header)
    if candidate is None or candidate.model_id != meta.model:
        return _e2ee_error("instance_unavailable", "instance is not currently available for this model")

    user = await session.get(User, db_key.user_id)
    if user is None:
        raise unauthorized("API key owner no longer exists")
    request_id = await _billing_request_id(session, user)
    usage_service = UsageService(session)
    # Reservation sizing (PROTOCOL.md: "max_tokens plus an input estimate equal to
    # the ciphertext length"). The prompt is ciphertext, so its token count is
    # unknown here. Ciphertext BYTES is a conservative estimate for real text (a
    # token is several bytes of JSON; gzip rarely gets below ~1 byte/token, and the
    # ~1.1 KB HPKE overhead only adds to it) -- but NOT a hard ceiling: pathological,
    # highly repetitive input can compress further. That is safe, because
    # settlement bills the enclave's REAL counts regardless (settle re-splits a
    # larger charge against allowance/wallet); the estimate only sizes what is held
    # and what the key's spend caps are checked against. The completion side is
    # the bound max_tokens, which the enclave itself enforces.
    estimate_prompt = len(body)
    usage_event = await usage_service.reserve_inference_tokens(
        user,
        api_key_id=db_key.id,
        model=meta.model,
        prompt_tokens=estimate_prompt,
        max_completion_tokens=meta.max_tokens,
        request_id=request_id,
    )
    await session.commit()
    reservation_id = usage_event.id

    forwarder = _make_forwarder(session)
    headers = meta.to_headers()
    try:
        bearer = await forwarder.active_bearer(candidate)
        exchange = await forwarder.e2ee_invoke(candidate, bearer, body, headers, request_id=request_id)
    except ForwardError as exc:
        return await _fail_before_response(session, usage_service, usage_event, forwarder, candidate, exc, request_id)
    except Exception:
        await usage_service.release_inference_reservation(usage_event)
        await session.commit()
        raise

    response = exchange.response
    extra_headers = {
        H_BILLING_REQUEST_ID: request_id,
        **_receipt_header(candidate, request_id=request_id, model=meta.model),
    }

    # Plaintext protocol error from the enclave (e.g. nonce_unknown on replay):
    # nothing ran, so nothing is charged; relay it verbatim.
    if response.headers.get(H_ERROR):
        try:
            content = await response.aread()
        except Exception:  # noqa: BLE001 - treated as an unreachable enclave below
            content = None
        finally:
            await exchange.aclose()
        if content is None:
            return await _fail_before_response(
                session, usage_service, usage_event, forwarder, candidate,
                ForwardError("enclave error body unreadable", latency_ms=exchange.latency_ms), request_id,
            )
        await usage_service.release_inference_reservation(usage_event)
        if response.status_code >= 500:
            # e2ee_unavailable & co. are the enclave's fault; 4xx protocol errors
            # are the caller's (bad metadata, replayed nonce) and must not let a
            # client tank a miner's reputation by sending garbage.
            await stats_recorder.record_failure(
                session, candidate,
                ForwardError("enclave protocol error", outcome=OUTCOME_HTTP_ERROR,
                             latency_ms=exchange.latency_ms, http_status=response.status_code),
                request_id=request_id,
            )
        await session.commit()
        passthrough = {k: v for k in _E2EE_ERROR_HEADERS if (v := response.headers.get(k)) is not None}
        return Response(
            content=content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
            headers={**passthrough, H_BILLING_REQUEST_ID: request_id},
        )

    # Anything that is neither a protocol error nor a sealed response (wrong
    # route, auth failure at the enclave proxy, a 502 from the miner's SNI
    # proxy...) means the enclave never handled the request: retryable elsewhere.
    if response.headers.get(H_ENCRYPTED) != "1":
        await exchange.aclose()
        return await _fail_before_response(
            session, usage_service, usage_event, forwarder, candidate,
            ForwardError(f"enclave returned {response.status_code} without a sealed body",
                         outcome=OUTCOME_HTTP_ERROR, latency_ms=exchange.latency_ms,
                         http_status=response.status_code),
            request_id,
        )

    if response.headers.get("content-type", "").startswith("text/event-stream"):
        return _relay_stream(
            request=request,
            exchange=exchange,
            db_key=db_key,
            meta=meta,
            reservation_id=reservation_id,
            request_id=request_id,
            estimate_prompt=estimate_prompt,
            extra_headers=extra_headers,
        )

    # Complete sealed body: non-streaming, or an engine error (any status) that the
    # enclave sealed before any stream started.
    try:
        content = await response.aread()
    except Exception as exc:  # noqa: BLE001 - mid-body failure: nothing was relayed yet
        await exchange.aclose()
        return await _fail_before_response(
            session, usage_service, usage_event, forwarder, candidate,
            ForwardError(f"enclave body read failed: {exc}", latency_ms=exchange.latency_ms), request_id,
        )
    await exchange.aclose()
    usage = usage_from_e2ee(response.headers.get(H_USAGE))
    status_code = response.status_code
    if usage is not None:
        prompt, completion = usage.prompt_tokens, usage.completion_tokens
    elif status_code < 400:
        # Mirrors the plaintext missing-usage fallback (which settles on its own
        # estimate rather than serving for free); here the only estimate is the
        # reservation itself.
        prompt, completion = estimate_prompt, meta.max_tokens
    else:
        prompt = completion = None
    try:
        if prompt is None:
            await usage_service.release_inference_reservation(usage_event)
        else:
            usage_event = await usage_service.settle_inference_usage(
                usage_event, prompt_tokens=prompt, completion_tokens=completion
            )
            usage_event.miner_id = candidate.miner_id
            usage_event.miner_hotkey = candidate.hotkey
            usage_event.miner_model_hash = candidate.model_hash
            session.add(usage_event)
            await APIKeyService(session).record_usage(
                db_key, request, input_tokens=prompt, output_tokens=completion, request_id=request_id
            )
    except Exception:
        # Same as execute_inference: never leave the reservation dangling.
        await usage_service.release_inference_reservation(usage_event)
        await session.commit()
        raise
    if status_code < 400:
        await stats_recorder.record_attempt(
            session, candidate, outcome=OUTCOME_SUCCESS, request_id=request_id,
            http_status=status_code, latency_ms=exchange.latency_ms,
            prompt_tokens=prompt or 0, completion_tokens=completion or 0,
            total_tokens=(usage.total_tokens if usage else 0) or ((prompt or 0) + (completion or 0)),
        )
    elif status_code >= 500:
        await stats_recorder.record_failure(
            session, candidate,
            ForwardError("enclave engine error", outcome=OUTCOME_HTTP_ERROR,
                         latency_ms=exchange.latency_ms, http_status=status_code),
            request_id=request_id,
        )
    await session.commit()
    relay_headers = {H_ENCRYPTED: "1", **extra_headers}
    if response.headers.get(H_USAGE):
        relay_headers[H_USAGE] = response.headers[H_USAGE]
    return Response(
        content=content,
        status_code=status_code,
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers=relay_headers,
    )


async def _fail_before_response(
    session: AsyncSession,
    usage_service: UsageService,
    usage_event: InferenceUsageEvent,
    forwarder: TEEForwarder,
    candidate: MinerCandidate,
    exc: ForwardError,
    request_id: str,
) -> JSONResponse:
    """The enclave failed before any response byte: release, record, 503 retry."""
    await usage_service.release_inference_reservation(usage_event)
    if exc.outcome == OUTCOME_PIN_MISMATCH:
        await forwarder.mark_stale(candidate.miner_id)
    await stats_recorder.record_failure(session, candidate, exc, request_id=request_id)
    await session.commit()
    logger.info(
        "e2ee.invoke_failed",
        extra={"miner_model_id": str(candidate.miner_model_id), "outcome": exc.outcome},
    )
    response = _e2ee_error("instance_unavailable", "instance failed before responding; re-encrypt to another")
    response.headers[H_BILLING_REQUEST_ID] = request_id
    return response


def _relay_stream(
    *,
    request: Request,
    exchange: EnclaveExchange,
    db_key: APIKey,
    meta: RequestMeta,
    reservation_id,
    request_id: str,
    estimate_prompt: int,
    extra_headers: dict[str, str],
) -> StreamingResponse:
    """Relay the sealed SSE stream byte-for-byte; settle in ``finally``.

    Mirrors ``product_routes._routed_stream_response``: the response outlives the
    route handler, so settlement uses its own session; the usage comes from the
    enclave's plaintext ``{"usage": ...}`` event, and the HYP-56 rule bills
    ~1 token per relayed sealed chunk (capped at max_tokens) when the client hung
    up after receiving content but before that event.
    """
    candidate = exchange.candidate

    async def body_iterator():
        try:
            async for chunk in exchange.aiter():
                yield chunk
        finally:
            from inference_api.db import async_session_maker

            usage = exchange.usage
            async with async_session_maker() as settle_session:
                svc = UsageService(settle_session)
                event = await settle_session.get(InferenceUsageEvent, reservation_id)
                if event is not None and event.status == "reserved":
                    completion = usage.completion_tokens or 0
                    prompt = usage.prompt_tokens or estimate_prompt
                    if not (completion or usage.total_tokens) and exchange.content_chunks:
                        completion = min(exchange.content_chunks, meta.max_tokens)
                    if completion or usage.total_tokens:
                        event = await svc.settle_inference_usage(
                            event, prompt_tokens=prompt, completion_tokens=completion
                        )
                        event.miner_id = candidate.miner_id
                        event.miner_hotkey = candidate.hotkey
                        event.miner_model_hash = candidate.model_hash
                        settle_session.add(event)
                        await stats_recorder.record_stream_success(
                            settle_session, exchange, request_id=request_id
                        )
                        await APIKeyService(settle_session).record_usage(
                            db_key,
                            request,
                            input_tokens=prompt,
                            output_tokens=completion,
                            request_id=request_id,
                        )
                    else:
                        await svc.release_inference_reservation(event)
                    await settle_session.commit()

    return StreamingResponse(
        body_iterator(),
        status_code=exchange.response.status_code,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            H_ENCRYPTED: "1",
            **extra_headers,
        },
    )
