from __future__ import annotations

import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import unauthorized
from inference_api.models import ServiceClient
from inference_api.redis import get_redis
from inference_api.router_service import RouterInferenceService
from inference_api.schemas import (
    RouterReservationCreate,
    RouterReservationResponse,
    RouterUsageSettle,
    RouterValidationResponse,
)
from inference_api.service_clients import current_router_client

router = APIRouter(prefix="/router/inference", tags=["router"])


async def raw_user_api_key_from_header(
    x_user_api_key: str | None = Header(default=None, alias="X-User-API-Key"),
) -> str:
    if not x_user_api_key:
        raise unauthorized("User API key required")
    return x_user_api_key


@router.get("/validate", response_model=RouterValidationResponse)
async def validate_inference_request(
    model: str = Query(..., min_length=1, max_length=120),
    input_tokens: int = Query(..., ge=1, le=settings.router_max_input_tokens),
    max_output_tokens: int = Query(..., ge=1, le=settings.router_max_output_tokens),
    raw_user_api_key: str = Depends(raw_user_api_key_from_header),
    session: AsyncSession = Depends(get_async_session),
    router_client: ServiceClient = Depends(current_router_client),
) -> RouterValidationResponse:
    response = await RouterInferenceService(session).validate_request(
        raw_user_api_key=raw_user_api_key,
        model=model,
        input_tokens=input_tokens,
        max_output_tokens=max_output_tokens,
    )
    await session.commit()
    return response


@router.post(
    "/reservations",
    response_model=RouterReservationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inference_reservation(
    payload: RouterReservationCreate,
    raw_user_api_key: str = Depends(raw_user_api_key_from_header),
    session: AsyncSession = Depends(get_async_session),
    router_client: ServiceClient = Depends(current_router_client),
    redis_client: redis.Redis = Depends(get_redis),
) -> RouterReservationResponse:
    response = await RouterInferenceService(session).create_reservation(
        router_client=router_client,
        raw_user_api_key=raw_user_api_key,
        payload=payload,
        redis_client=redis_client,
    )
    await session.commit()
    return response


@router.post("/usage", response_model=RouterReservationResponse)
async def settle_inference_usage(
    request: Request,
    payload: RouterUsageSettle,
    raw_user_api_key: str = Depends(raw_user_api_key_from_header),
    session: AsyncSession = Depends(get_async_session),
    router_client: ServiceClient = Depends(current_router_client),
) -> RouterReservationResponse:
    response = await RouterInferenceService(session).settle_usage(
        request=request,
        router_client=router_client,
        raw_user_api_key=raw_user_api_key,
        payload=payload,
    )
    await session.commit()
    return response


@router.post("/reservations/{reservation_id}/release", response_model=RouterReservationResponse)
async def release_inference_reservation(
    reservation_id: uuid.UUID,
    raw_user_api_key: str = Depends(raw_user_api_key_from_header),
    session: AsyncSession = Depends(get_async_session),
    router_client: ServiceClient = Depends(current_router_client),
) -> RouterReservationResponse:
    response = await RouterInferenceService(session).release_reservation(
        router_client=router_client,
        raw_user_api_key=raw_user_api_key,
        reservation_id=reservation_id,
    )
    await session.commit()
    return response
