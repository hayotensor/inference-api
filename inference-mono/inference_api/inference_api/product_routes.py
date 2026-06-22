from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.api_keys import APIKeyService, require_api_key
from inference_api.db import get_async_session
from inference_api.errors import bad_request, not_found, unauthorized
from inference_api.mesh import MeshInferenceClient
from inference_api.models import APIKey, User
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


async def execute_inference(
    *,
    request: Request,
    payload: InferenceRequest,
    session: AsyncSession,
    db_key: APIKey,
) -> InferenceExecution:
    user = await session.get(User, db_key.user_id)
    if user is None:
        raise unauthorized("API key owner no longer exists")
    input_tokens = max(1, len(payload.prompt.split()))
    usage_service = UsageService(session)
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
    await session.commit()
    return ModelListResponse(data=AVAILABLE_MODELS)


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
