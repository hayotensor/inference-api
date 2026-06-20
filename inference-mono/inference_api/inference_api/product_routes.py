from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.api_keys import APIKeyService, require_api_key
from inference_api.db import get_async_session
from inference_api.errors import unauthorized
from inference_api.mesh import MeshInferenceClient
from inference_api.models import APIKey, User
from inference_api.schemas import APIKeyUsageSummary, InferenceRequest, InferenceResponse, ModelInfo, ModelListResponse
from inference_api.usage import UsageService

router = APIRouter(prefix="/v1", tags=["product-api"])


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["models:read"])),
) -> ModelListResponse:
    await APIKeyService(session).record_usage(db_key, request)
    await session.commit()
    return ModelListResponse(
        data=[
            ModelInfo(id="demo-inference-001", owned_by="inference-api"),
            ModelInfo(id="demo-chat-001", owned_by="inference-api"),
        ]
    )


@router.post("/inference", response_model=InferenceResponse)
async def run_inference(
    request: Request,
    payload: InferenceRequest,
    session: AsyncSession = Depends(get_async_session),
    db_key: APIKey = Depends(require_api_key(["inference:write"])),
) -> InferenceResponse:
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
    return InferenceResponse(
        id=f"inf_{db_key.id.hex[:24]}",
        model=payload.model,
        output=output,
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "charged_tokens": usage_event.charged_tokens,
            "remaining_tokens": period.remaining_tokens,
        },
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
