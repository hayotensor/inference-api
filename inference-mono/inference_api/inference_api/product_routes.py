from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.api_keys import APIKeyService, require_api_key
from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import bad_request, not_found, service_unavailable, unauthorized
from inference_api.mesh import MeshInferenceClient
from inference_api.miners.service import MinerRegistryService
from inference_api.models import APIKey, User
from inference_api.routing.forwarder import ForwardError, TEEForwarder, Usage
from inference_api.routing.selector import MinerCandidate, MinerSelector
from inference_api.schemas import (
    APIKeyUsageSummary,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    ModelListResponse,
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
)
from inference_api.usage import UsageService

router = APIRouter(prefix="/v1", tags=["product-api"])

AVAILABLE_MODELS = [
    ModelInfo(id="demo-inference-001", owned_by="inference-api"),
    ModelInfo(id="demo-chat-001", owned_by="inference-api"),
]

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

    @property
    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "charged_tokens": self.charged_tokens,
            "remaining_tokens": self.remaining_tokens,
        }


def model_by_id(model_id: str) -> ModelInfo | None:
    return next((model for model in AVAILABLE_MODELS if model.id == model_id), None)


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

    When no attested+healthy miner hosts the requested model AND we are not in
    production, falls back to the in-process mesh echo (dev convenience). The
    SAME single reservation is reused across all failover candidates; settlement
    always uses the REAL token counts the enclave reports.
    """
    user = await session.get(User, db_key.user_id)
    if user is None:
        raise unauthorized("API key owner no longer exists")
    input_tokens = max(1, len(payload.prompt.split()))
    usage_service = UsageService(session)

    candidates = await MinerSelector(session).select(payload.model)

    if not candidates:
        if settings.app_env == "production":
            raise service_unavailable(
                "no_capacity", f"No attested miner is serving model {payload.model!r}"
            )
        return await _execute_inference_mesh_fallback(
            request=request,
            payload=payload,
            session=session,
            db_key=db_key,
            user=user,
            usage_service=usage_service,
            input_tokens=input_tokens,
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
    last_error: Exception | None = None
    try:
        for candidate in candidates[: settings.forward_max_attempts]:
            try:
                result = await forwarder.forward(candidate, chat_payload)
            except ForwardError as exc:
                last_error = exc
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
        usage_event = await usage_service.settle_inference_usage(
            usage_event,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
        await APIKeyService(session).record_usage(
            db_key,
            request,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
        charged_tokens=usage_event.charged_tokens,
        remaining_tokens=period.remaining_tokens,
    )


def openai_usage(execution: InferenceExecution) -> dict[str, int]:
    return {
        "prompt_tokens": execution.input_tokens,
        "completion_tokens": execution.output_tokens,
        "total_tokens": execution.input_tokens + execution.output_tokens,
    }


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
    await APIKeyService(session).record_usage(db_key, request)
    model_ids = await MinerRegistryService(session).list_available_models()
    await session.commit()
    if model_ids:
        data = [ModelInfo(id=model_id, owned_by="talaris-miner") for model_id in model_ids]
    elif settings.app_env != "production":
        # Dev fallback: no registered miners yet — advertise the demo models.
        data = AVAILABLE_MODELS
    else:
        data = []
    return ModelListResponse(data=data)


@router.get("/models/{model_id}", response_model=ModelInfo)
async def get_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["models:read"])),
) -> ModelInfo:
    model = model_by_id(model_id)
    if model is None:
        raise not_found("Model not found")
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
        id=f"inf_{db_key.id.hex[:24]}",
        model=payload.model,
        output=execution.output,
        usage=execution.usage,
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
    forwarder = _make_forwarder(session)
    chat_payload = _chat_payload_from_prompt(inference_payload)

    stream = None
    last_error: Exception | None = None
    for candidate in candidates[: settings.forward_max_attempts]:
        try:
            stream = await forwarder.forward_stream(candidate, chat_payload)
            break
        except ForwardError as exc:
            last_error = exc
            continue
    if stream is None:
        await usage_service.release_inference_reservation(usage_event)
        await session.commit()
        raise service_unavailable(
            "forward_failed", f"All miners failed for streaming: {last_error}"
        )

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
                    else:
                        # Client disconnected before any usage was seen: release.
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
    inference_payload = InferenceRequest(
        model=payload.model,
        prompt=prompt,
        max_tokens=payload.resolved_max_tokens,
    )
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
    service = APIKeyService(session)
    await service.record_usage(db_key, request)
    summary = await service.usage_summary(db_key)
    await session.commit()
    return summary
