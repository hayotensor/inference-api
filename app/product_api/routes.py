from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys.dependencies import require_api_key
from app.api_keys.schemas import APIKeyUsageSummary
from app.api_keys.service import APIKeyService
from app.db.models.api_key import APIKey
from app.db.session import get_async_session

router = APIRouter(prefix="/v1", tags=["product-api"])


class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo]


class InferenceRequest(BaseModel):
    model: str = "demo-inference-001"
    prompt: str = Field(min_length=1, max_length=20_000)
    max_tokens: int = Field(default=128, ge=1, le=4096)


class InferenceResponse(BaseModel):
    id: str
    object: Literal["inference"] = "inference"
    model: str
    output: str
    usage: dict[str, int]


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
    input_tokens = max(1, len(payload.prompt.split()))
    output = f"Echo: {payload.prompt[: min(len(payload.prompt), payload.max_tokens)]}"
    output_tokens = max(1, len(output.split()))
    await APIKeyService(session).record_usage(
        db_key,
        request,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    await session.commit()
    return InferenceResponse(
        id=f"inf_{db_key.id.hex[:24]}",
        model=payload.model,
        output=output,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
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
    return APIKeyUsageSummary.model_validate(summary)
