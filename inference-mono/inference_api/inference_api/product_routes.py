from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import miner_hash as compute_miner_hash

from inference_api.api_keys import APIKeyService, require_api_key
from inference_api.attestation import sign_inference_receipt
from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import bad_request, forbidden, not_found, service_unavailable, unauthorized
from inference_api.mesh import MeshInferenceClient
from inference_api.miners.service import MinerRegistryService
from inference_api.models import APIKey, Miner, MinerModel, User
from inference_api.provisioner.key import load_provisioner_signing_key
from inference_api.routing.forwarder import ForwardError, TEEForwarder, Usage
from inference_api.routing.selector import MinerCandidate, MinerSelector
from inference_api import stats_recorder
from inference_api.schemas import (
    PROMPT_MAX_LENGTH,
    APIKeyUsageSummary,
    AttestationReceipt,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    ModelListResponse,
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
)
from inference_api.usage import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["product-api"])

# Injectable so tests can route forwarding through the in-process dev-TEE transport.
_forwarder_transport = None


def set_forwarder_transport(transport) -> None:
    """Test hook: route all enclave forwarding through ``transport`` (bypasses pin)."""
    global _forwarder_transport
    _forwarder_transport = transport


def _make_forwarder(session) -> TEEForwarder:
    return TEEForwarder(session, transport=_forwarder_transport)


@dataclass(frozen=True)
class InferenceExecution:
    output: str
    input_tokens: int
    output_tokens: int
    charged_tokens: int
    remaining_tokens: int
    # The same per-request id used for stats/attestation (request_id_ctx, or a
    # fresh uuid4 if the caller sent no X-Request-ID) -- NOT a function of the
    # API key, and unique across requests even from the same key. See HYP-40.
    request_id: str
    # False only when the opt-in dev echo fallback answered instead of a real
    # attested miner. See _execute_inference_mesh_fallback.
    attested: bool = True
    # Populated iff `attested` -- a platform-signed, independently-verifiable
    # receipt naming the exact enclave identity that served this response. See
    # inference_api.attestation and _attestation_receipt_for below.
    attestation: AttestationReceipt | None = None

    @property
    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "charged_tokens": self.charged_tokens,
            "remaining_tokens": self.remaining_tokens,
        }


# A miner in this state is gone for good, not briefly unavailable, so the models
# it advertised must not linger in the catalog as "degraded" -- otherwise a
# customer picks a model that can never be served. Anything else (pending,
# verifying, stale, failed) is retryable and stays listed.
_TERMINAL_MINER_STATES = ("revoked",)


async def _all_registered_model_ids(session: AsyncSession) -> set[str]:
    """Distinct model ids currently advertised by a NON-terminal miner.

    ``MinerRegistryService.list_available_models`` only returns the fully
    eligible subset (attested + healthy + loaded + active token), so it alone
    cannot tell "this model was never registered on the platform" apart from
    "it's registered but nothing can serve it right now". Both this and
    ``_is_model_registered`` below answer the broader "registered" question by
    querying ``miner_models`` directly -- excluding revoked miners, so a model
    whose only host was revoked reads as never-registered (404) rather than as
    temporarily degraded.
    """
    result = await session.execute(
        select(MinerModel.model_id)
        .join(Miner, Miner.id == MinerModel.miner_id)
        .where(Miner.attestation_status.notin_(_TERMINAL_MINER_STATES))
        .distinct()
    )
    return {row[0] for row in result.all()}


async def _is_model_registered(session: AsyncSession, model_id: str) -> bool:
    """Whether a non-terminal miner advertises ``model_id``."""
    result = await session.execute(
        select(MinerModel.id)
        .join(Miner, Miner.id == MinerModel.miner_id)
        .where(
            MinerModel.model_id == model_id,
            Miner.attestation_status.notin_(_TERMINAL_MINER_STATES),
        )
        .limit(1)
    )
    return result.first() is not None


def _model_not_found(model: str) -> HTTPException:
    """A distinct, machine-readable 404 for "never registered by any miner".

    Mirrors ``errors.not_found``'s envelope shape/status code exactly, but with
    code ``model_not_found`` (rather than the helper's fixed ``not_found``) so a
    client can tell this apart from ``no_capacity`` (registered, but nothing can
    serve it right now) without string-matching the message.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "model_not_found", "message": f"Model {model!r} is not hosted by any registered miner"},
    )


def _check_model_allowed(db_key: APIKey, model_id: str) -> None:
    """Enforce an API key's optional model allowlist.

    ``db_key.allowed_models`` is ``None``/empty for the common case (an
    unrestricted key, matching every key created before this existed), so this
    is a no-op unless the dashboard user opted into scoping the key down.
    Checked before ``_model_not_found``/``no_capacity`` so a caller can't use
    the error code to distinguish "never registered" from "registered but not
    on your key's allowlist".
    """
    allowed = db_key.allowed_models
    if allowed and model_id not in allowed:
        raise forbidden(f"This API key is not permitted to use model {model_id!r}")


def _prompt_too_long_error(prompt: str, exc: ValidationError) -> HTTPException:
    """Translate an ``InferenceRequest`` construction failure into a clean 4xx.

    ``create_chat_completion`` builds an ``InferenceRequest`` by hand (it is not
    a FastAPI-bound route parameter here, so pydantic's ``ValidationError``
    would otherwise propagate as an unhandled 500). The realistic failure is the
    combined message content exceeding ``PROMPT_MAX_LENGTH``, which we surface
    as 413 (Payload Too Large: the request is well-formed, it's just bigger
    than the server will process) naming the exact limit. Any other, unexpected
    validation failure still becomes a clean 422 instead of a 500.
    """
    too_long = any(
        error.get("loc") == ("prompt",) and error.get("type") == "string_too_long"
        for error in exc.errors()
    )
    if too_long:
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "prompt_too_long",
                "message": (
                    f"Combined message content is {len(prompt)} characters, which "
                    f"exceeds the maximum prompt length of {PROMPT_MAX_LENGTH} "
                    "characters. Shorten the messages and retry."
                ),
            },
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "invalid_request", "message": str(exc)},
    )


def text_from_openai_content(content: str | list[dict[str, object]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    text_parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
    return "\n".join(text_parts)


def prompt_from_openai_messages(messages: list[OpenAIChatMessage]) -> str:
    prompt_parts: list[str] = []
    for message in messages:
        content = text_from_openai_content(message.content).strip()
        if content:
            prompt_parts.append(f"{message.role}: {content}")

    if not prompt_parts:
        raise bad_request("invalid_messages", "At least one message must include text content")
    return "\n\n".join(prompt_parts)


def _chat_payload_from_prompt(payload: InferenceRequest) -> dict:
    """Build an OpenAI chat-completions body the enclave understands."""
    return {
        "model": payload.model,
        "messages": [{"role": "user", "content": payload.prompt}],
        "max_tokens": payload.max_tokens,
    }


def _attestation_receipt_for(candidate: MinerCandidate, *, request_id: str, model_id: str) -> AttestationReceipt:
    """Sign a per-request attestation receipt naming ``candidate``'s enclave identity.

    Uses the SAME platform Ed25519 key that signs ``GET /allowlist`` and
    ``GET /subnet/stats/*`` (``load_provisioner_signing_key``), so a caller who
    already pins that key (from ``GET /attest``) can verify this receipt with no
    additional trust anchor. See inference_api.attestation for the canonical
    message + verification function.
    """
    try:
        miner_hash_hex = compute_miner_hash(candidate.hotkey) if candidate.hotkey else None
    except (ValueError, TypeError):
        # A malformed/non-hex hotkey should never crash a served response --
        # the receipt is still signed, just without this optional field.
        miner_hash_hex = None
    return sign_inference_receipt(
        request_id=request_id,
        model_id=model_id,
        model_hash=candidate.model_hash,
        enclave_verify_key=candidate.enclave_verify_key,
        tls_cert_fingerprint=candidate.tls_cert_fingerprint,
        miner_hash=miner_hash_hex,
        attestation_mode=candidate.attestation_mode,
        attestation_verified_at=candidate.attestation_verified_at_unix,
        signing_key=load_provisioner_signing_key(),
    )


def _text_from_completion(body: dict | None) -> str:
    if not isinstance(body, dict):
        return ""
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    return ""


async def execute_inference(
    *,
    request: Request,
    payload: InferenceRequest,
    session: AsyncSession,
    db_key: APIKey,
) -> InferenceExecution:
    """Reserve -> forward (to an attested miner, with failover) -> settle.

    When no attested+healthy miner hosts the requested model, this fails with a
    proper error in EVERY environment: 404 ``model_not_found`` if no miner has
    ever registered the model at all, else 503 ``no_capacity``. The only way to
    get the in-process mesh echo (dev convenience) instead is the explicit
    ``settings.allow_dev_echo_fallback`` opt-in (default off, and refused
    outright in production by config.py's validator).

    The SAME single reservation is reused across all failover candidates;
    settlement always uses the REAL token counts the enclave reports.
    """
    _check_model_allowed(db_key, payload.model)
    user = await session.get(User, db_key.user_id)
    if user is None:
        raise unauthorized("API key owner no longer exists")
    input_tokens = max(1, len(payload.prompt.split()))
    usage_service = UsageService(session)

    candidates = await MinerSelector(session).select(payload.model)

    if not candidates:
        if settings.allow_dev_echo_fallback:
            return await _execute_inference_mesh_fallback(
                request=request,
                payload=payload,
                session=session,
                db_key=db_key,
                user=user,
                usage_service=usage_service,
                input_tokens=input_tokens,
            )
        if not await _is_model_registered(session, payload.model):
            raise _model_not_found(payload.model)
        raise service_unavailable(
            "no_capacity", f"No attested miner is serving model {payload.model!r}"
        )

    # Single reservation reused across all failover candidates.
    usage_event = await usage_service.reserve_inference_tokens(
        user,
        api_key_id=db_key.id,
        model=payload.model,
        prompt_tokens=input_tokens,
        max_completion_tokens=payload.max_tokens + 1,
    )
    forwarder = _make_forwarder(session)
    chat_payload = _chat_payload_from_prompt(payload)
    # The id the enclave will stamp onto its usage row, so the miner-statistics row
    # written below can later be reconciled against the enclave's signed receipt.
    request_id = usage_event.request_id
    last_error: Exception | None = None
    try:
        for candidate in candidates[: settings.forward_max_attempts]:
            try:
                result = await forwarder.forward(candidate, chat_payload, request_id=request_id)
            except ForwardError as exc:
                last_error = exc
                # Record the failed attempt against THIS miner before failing over.
                # Dropping it (the previous behavior) made an unreliable miner
                # indistinguishable from one that was never tried.
                await stats_recorder.record_failure(
                    session, candidate, exc, request_id=request_id
                )
                continue
            usage = result.usage
            output = _text_from_completion(result.body)
            output_tokens = usage.completion_tokens or max(1, len(output.split()))
            real_prompt_tokens = usage.prompt_tokens or input_tokens
            usage_event = await usage_service.settle_inference_usage(
                usage_event,
                prompt_tokens=real_prompt_tokens,
                completion_tokens=output_tokens,
            )
            # Stamp the serving miner onto the usage event for attribution.
            usage_event.miner_id = candidate.miner_id
            usage_event.miner_hotkey = candidate.hotkey
            usage_event.miner_model_hash = candidate.model_hash
            session.add(usage_event)
            # Record the served attempt with the SETTLED counts so the statistics
            # row and the billing row can never disagree about this request.
            await stats_recorder.record_success(
                session,
                result,
                request_id=request_id,
                prompt_tokens=real_prompt_tokens,
                completion_tokens=output_tokens,
            )
            await APIKeyService(session).record_usage(
                db_key,
                request,
                input_tokens=real_prompt_tokens,
                output_tokens=output_tokens,
            )
            period = await usage_service.current_period(user)
            await session.commit()
            return InferenceExecution(
                output=output,
                input_tokens=real_prompt_tokens,
                output_tokens=output_tokens,
                charged_tokens=usage_event.charged_tokens,
                remaining_tokens=period.remaining_tokens,
                request_id=request_id,
                attestation=_attestation_receipt_for(
                    candidate, request_id=request_id, model_id=payload.model
                ),
            )
        # All candidates failed.
        await usage_service.release_inference_reservation(usage_event)
        await session.commit()
    except Exception:
        await usage_service.release_inference_reservation(usage_event)
        await session.commit()
        raise
    raise service_unavailable(
        "forward_failed",
        f"All miners failed for model {payload.model!r}: {last_error}",
    )


async def _execute_inference_mesh_fallback(
    *,
    request: Request,
    payload: InferenceRequest,
    session: AsyncSession,
    db_key: APIKey,
    user: User,
    usage_service: UsageService,
    input_tokens: int,
) -> InferenceExecution:
    """Dev-only echo fallback. Only reachable via ``settings.allow_dev_echo_fallback``.

    Deliberately NEVER billed: this output is fabricated (the TEE is bypassed
    entirely), so charging a user real inference-token quota for it would be
    fraudulent regardless of environment. The reservation is taken (so the
    request participates in the same reserve/settle plumbing and any
    insufficient-balance check still applies) but then released, not settled,
    so ``charged_tokens`` is always 0 and the user's remaining balance is
    unaffected. The request is still recorded in the API-key usage log (for
    operator audit visibility that a call happened) but with zero input/output
    tokens, so it cannot inflate the token counters ``GET /v1/usage`` reports
    for real inference work.
    """
    logger.warning(
        "dev_echo_fallback.served model=%r api_key_id=%s user_id=%s: no attested "
        "miner is available for this model; the response below is a FABRICATED, "
        "UNATTESTED echo (the TEE was bypassed) and is NOT billed",
        payload.model,
        db_key.id,
        user.id,
    )
    usage_event = await usage_service.reserve_inference_tokens(
        user,
        api_key_id=db_key.id,
        model=payload.model,
        prompt_tokens=input_tokens,
        max_completion_tokens=payload.max_tokens + 1,
    )
    try:
        mesh_result = await MeshInferenceClient().run(payload)
        output = mesh_result.output
        output_tokens = mesh_result.output_tokens or max(1, len(output.split()))
        await usage_service.release_inference_reservation(usage_event)
        await APIKeyService(session).record_usage(
            db_key,
            request,
            input_tokens=0,
            output_tokens=0,
        )
    except Exception:
        await usage_service.release_inference_reservation(usage_event)
        await session.commit()
        raise
    period = await usage_service.current_period(user)
    await session.commit()
    return InferenceExecution(
        output=output,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        charged_tokens=0,
        remaining_tokens=period.remaining_tokens,
        request_id=usage_event.request_id,
        attested=False,
    )


def openai_usage(execution: InferenceExecution) -> dict[str, int]:
    return {
        "prompt_tokens": execution.input_tokens,
        "completion_tokens": execution.output_tokens,
        "total_tokens": execution.input_tokens + execution.output_tokens,
    }


def _system_fingerprint(execution: InferenceExecution) -> str | None:
    """OpenAI-compatible, machine-readable "not real model output" stamp.

    ``system_fingerprint`` is a real, nullable field in the OpenAI chat-
    completions schema (meant to identify the backend configuration that
    served the request), so setting it keeps every response OpenAI-schema-
    compatible and lets a client detect the dev echo fallback programmatically
    (``system_fingerprint == "dev-echo-unattested"``) without parsing message
    content. Real attested completions leave it ``null``, same as OpenAI does
    when it has nothing to report.
    """
    return None if execution.attested else "dev-echo-unattested"


def chat_completion_response(
    *,
    completion_id: str,
    created: int,
    model: str,
    output: str,
    execution: InferenceExecution,
) -> dict[str, object]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": output,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": openai_usage(execution),
        "system_fingerprint": _system_fingerprint(execution),
        # Non-standard additive field (OpenAI clients ignore unknown keys):
        # mirrors InferenceResponse.attestation -- a platform-signed receipt a
        # third party can verify against the pinned key from GET /attest. See
        # inference_api.attestation.verify_inference_receipt.
        "talaris_attestation": execution.attestation.model_dump() if execution.attestation else None,
    }


def sse_data(payload: dict[str, object] | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def chat_completion_stream(
    *,
    completion_id: str,
    created: int,
    model: str,
    output: str,
    execution: InferenceExecution,
    include_usage: bool,
):
    base_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "usage": None,
        "system_fingerprint": _system_fingerprint(execution),
    }
    yield sse_data(
        {
            **base_chunk,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
    )
    if output:
        yield sse_data(
            {
                **base_chunk,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": output},
                        "finish_reason": None,
                    }
                ],
            }
        )
    yield sse_data(
        {
            **base_chunk,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
    )
    if include_usage:
        yield sse_data(
            {
                **base_chunk,
                "choices": [],
                "usage": openai_usage(execution),
            }
        )
    yield sse_data("[DONE]")


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["models:read"])),
) -> ModelListResponse:
    """Honest model catalog: real registered models only, never demo/static ids.

    A model whose miner(s) went stale/unattested is kept in the list (marked
    ``status="degraded"``) instead of silently disappearing, so a customer sees
    *why* a model they used yesterday can't be reached today rather than it
    just vanishing. See ``ModelInfo.status`` and A1's model_not_found/no_capacity
    split, which this same registered-vs-available distinction underpins.
    """
    await APIKeyService(session).record_usage(db_key, request)
    available_ids = set(await MinerRegistryService(session).list_available_models())
    all_ids = await _all_registered_model_ids(session)
    await session.commit()
    if db_key.allowed_models:
        # Scoped key: the catalog itself should match what it can actually
        # reach, not just the enforcement on /inference and /chat/completions.
        all_ids &= set(db_key.allowed_models)
    data = [
        ModelInfo(
            id=model_id,
            owned_by="talaris-miner",
            status="available" if model_id in available_ids else "degraded",
        )
        for model_id in sorted(all_ids)
    ]
    return ModelListResponse(data=data)


@router.get("/models/{model_id}", response_model=ModelInfo)
async def get_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["models:read"])),
) -> ModelInfo:
    if db_key.allowed_models and model_id not in db_key.allowed_models:
        # Matches list_models filtering the same model out of the catalog: a
        # scoped key sees an out-of-scope model as absent, not forbidden.
        raise not_found(f"Model {model_id!r} not found")
    available_ids = set(await MinerRegistryService(session).list_available_models())
    if model_id in available_ids:
        model = ModelInfo(id=model_id, owned_by="talaris-miner", status="available")
    elif await _is_model_registered(session, model_id):
        model = ModelInfo(id=model_id, owned_by="talaris-miner", status="degraded")
    else:
        raise not_found(f"Model {model_id!r} not found")
    await APIKeyService(session).record_usage(db_key, request)
    await session.commit()
    return model


@router.post("/inference", response_model=InferenceResponse)
async def run_inference(
    request: Request,
    payload: InferenceRequest,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["inference:write"])),
) -> InferenceResponse:
    execution = await execute_inference(request=request, payload=payload, session=session, db_key=db_key)
    return InferenceResponse(
        # Unique per request (not a function of the API key -- see HYP-40):
        # the same id used for stats/attestation, sourced from X-Request-ID or
        # a fresh uuid4, so it matches attestation.request_id when attested.
        id=f"inf_{execution.request_id}",
        model=payload.model,
        output=execution.output,
        usage=execution.usage,
        attested=execution.attested,
        attestation=execution.attestation,
    )


async def _routed_stream_response(
    *,
    request: Request,
    payload: OpenAIChatCompletionRequest,
    inference_payload: InferenceRequest,
    candidates: list[MinerCandidate],
    session: AsyncSession,
    db_key: APIKey,
    user: User,
):
    """Real streaming pass-through from an attested miner.

    Reserves ONCE, forwards a streaming request to the first working candidate,
    relays the raw SSE bytes, and settles from the accumulated real ``usage`` in
    a ``finally`` so the reservation is reconciled even on client disconnect.
    """
    usage_service = UsageService(session)
    input_tokens = max(1, len(inference_payload.prompt.split()))
    usage_event = await usage_service.reserve_inference_tokens(
        user,
        api_key_id=db_key.id,
        model=inference_payload.model,
        prompt_tokens=input_tokens,
        max_completion_tokens=inference_payload.max_tokens + 1,
    )
    await session.commit()
    reservation_id = usage_event.id
    request_id = usage_event.request_id
    forwarder = _make_forwarder(session)
    chat_payload = _chat_payload_from_prompt(inference_payload)

    stream = None
    last_error: Exception | None = None
    for candidate in candidates[: settings.forward_max_attempts]:
        try:
            stream = await forwarder.forward_stream(candidate, chat_payload, request_id=request_id)
            break
        except ForwardError as exc:
            last_error = exc
            await stats_recorder.record_failure(session, candidate, exc, request_id=request_id)
            continue
    if stream is None:
        await usage_service.release_inference_reservation(usage_event)
        # Commits the release AND the failed-attempt rows recorded above.
        await session.commit()
        raise service_unavailable(
            "forward_failed", f"All miners failed for streaming: {last_error}"
        )
    # Persist the failed attempts that preceded the working candidate; the request
    # session is not committed again on the success path (settlement uses its own).
    await session.commit()

    selected = stream.candidate

    async def body_iterator():
        # New session/usage_service so settlement is independent of the request
        # session lifecycle (the response outlives the route handler).
        try:
            async for chunk in stream.aiter():
                yield chunk
        finally:
            from inference_api.db import async_session_maker

            usage = stream.usage
            async with async_session_maker() as settle_session:
                svc = UsageService(settle_session)
                event = await settle_session.get(
                    type(usage_event), reservation_id
                )
                if event is not None and event.status == "reserved":
                    completion = usage.completion_tokens or 0
                    prompt = usage.prompt_tokens or input_tokens
                    if completion or usage.total_tokens:
                        event = await svc.settle_inference_usage(
                            event,
                            prompt_tokens=prompt,
                            completion_tokens=completion,
                        )
                        event.miner_id = selected.miner_id
                        event.miner_hotkey = selected.hotkey
                        event.miner_model_hash = selected.model_hash
                        settle_session.add(event)
                        # The stream is closed by now, so ``latency_ms``/``ttft_ms``
                        # on the StreamForward are final.
                        await stats_recorder.record_stream_success(
                            settle_session, stream, request_id=request_id
                        )
                        # Real streaming previously settled the billing ledger
                        # above but never recorded here, so a fully-drained
                        # streaming call billed correctly yet never moved
                        # GET /v1/usage's counters. Record it exactly once, with
                        # the same REAL settled counts, using an explicit
                        # request_id (not the ambient contextvar, which may no
                        # longer be valid by the time this ``finally`` runs).
                        await APIKeyService(settle_session).record_usage(
                            db_key,
                            request,
                            input_tokens=prompt,
                            output_tokens=completion,
                            request_id=request_id,
                        )
                    else:
                        # Client disconnected before any usage was seen: release.
                        # No statistics row — the miner served nothing measurable,
                        # and charging it a "failure" for the client's disconnect
                        # would be wrong.
                        await svc.release_inference_reservation(event)
                    await settle_session.commit()

    return StreamingResponse(
        body_iterator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/completions")
async def create_chat_completion(
    request: Request,
    payload: OpenAIChatCompletionRequest,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["inference:write"])),
):
    prompt = prompt_from_openai_messages(payload.messages)
    try:
        inference_payload = InferenceRequest(
            model=payload.model,
            prompt=prompt,
            max_tokens=payload.resolved_max_tokens,
        )
    except ValidationError as exc:
        # OpenAIChatCompletionRequest puts no length cap on message content, so
        # this manual InferenceRequest construction (unlike /v1/inference, where
        # FastAPI binds InferenceRequest directly and already turns a
        # ValidationError into a clean 422) is the only place an over-long
        # prompt would otherwise surface as an unhandled 500.
        raise _prompt_too_long_error(prompt, exc) from exc
    # Covers BOTH branches below: the streaming path calls MinerSelector directly
    # and never reaches execute_inference's own allowlist check.
    _check_model_allowed(db_key, payload.model)
    # Real streaming pass-through when an attested miner serves the model.
    if payload.stream:
        candidates = await MinerSelector(session).select(payload.model)
        if candidates:
            user = await session.get(User, db_key.user_id)
            if user is None:
                raise unauthorized("API key owner no longer exists")
            return await _routed_stream_response(
                request=request,
                payload=payload,
                inference_payload=inference_payload,
                candidates=candidates,
                session=session,
                db_key=db_key,
                user=user,
            )
    execution = await execute_inference(
        request=request,
        payload=inference_payload,
        session=session,
        db_key=db_key,
    )
    completion_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = int(time.time())
    if payload.stream:
        include_usage = bool(payload.stream_options and payload.stream_options.include_usage)
        return StreamingResponse(
            chat_completion_stream(
                completion_id=completion_id,
                created=created,
                model=payload.model,
                output=execution.output,
                execution=execution,
                include_usage=include_usage,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    return chat_completion_response(
        completion_id=completion_id,
        created=created,
        model=payload.model,
        output=execution.output,
        execution=execution,
    )


@router.get("/usage", response_model=APIKeyUsageSummary)
async def get_usage(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["usage:read"])),
) -> APIKeyUsageSummary:
    # Deliberately does NOT call record_usage here: APIKeyUsageSummary.requests
    # counts every row in the api_key_usage log, so recording this read as a
    # row made a plain GET /v1/usage inflate the very "requests" counter it
    # reports -- misleading, since that counter is meant to represent
    # inference requests, not reads of the usage summary itself.
    service = APIKeyService(session)
    summary = await service.usage_summary(db_key)
    await session.commit()
    return summary
