from __future__ import annotations

import uuid

import redis.asyncio as redis
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.api_keys import APIKeyService
from inference_api.config import settings
from inference_api.errors import bad_request, forbidden, not_found, unauthorized
from inference_api.models import APIKey, InferenceUsageEvent, ServiceClient, UsagePeriod, User
from inference_api.pricing import ModelPricingService
from inference_api.schemas import (
    RouterReservationCreate,
    RouterReservationResponse,
    RouterUsageSettle,
    RouterValidationResponse,
)
from inference_api.security import expires_in
from inference_api.usage import UsageService, charged_token_count


class RouterInferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def validate_request(
        self,
        *,
        raw_user_api_key: str,
        model: str,
        input_tokens: int,
        max_output_tokens: int,
    ) -> RouterValidationResponse:
        db_key, user = await self._authenticate_user_api_key(
            raw_user_api_key,
            redis_client=None,
            apply_rate_limit=False,
        )
        usage_service = UsageService(self.session)
        await usage_service.expire_stale_reservations(user.id)
        period = await usage_service.current_period(user)
        multiplier = await ModelPricingService(self.session).multiplier_for_model(model)
        estimated_tokens = input_tokens + max_output_tokens
        estimated_charged_tokens = charged_token_count(estimated_tokens, multiplier)
        allowed = period.remaining_tokens >= estimated_charged_tokens
        return RouterValidationResponse(
            allowed=allowed,
            reason="allowed" if allowed else "insufficient_credits",
            user_id=user.id,
            api_key_id=db_key.id,
            model=model,
            estimated_tokens=estimated_tokens,
            estimated_charged_tokens=estimated_charged_tokens,
            remaining_tokens=period.remaining_tokens,
            model_multiplier=multiplier,
        )

    async def create_reservation(
        self,
        *,
        router_client: ServiceClient,
        raw_user_api_key: str,
        payload: RouterReservationCreate,
        redis_client: redis.Redis | None,
    ) -> RouterReservationResponse:
        db_key, user = await self._authenticate_user_api_key(
            raw_user_api_key,
            redis_client=redis_client,
            apply_rate_limit=True,
        )
        usage_service = UsageService(self.session)
        event = await usage_service.reserve_inference_tokens(
            user,
            api_key_id=db_key.id,
            router_client_id=router_client.id,
            model=payload.model,
            prompt_tokens=payload.input_tokens,
            max_completion_tokens=payload.max_output_tokens,
            request_id=payload.request_id,
            expires_at=expires_in(seconds=settings.router_reservation_ttl_seconds),
        )
        if event.status == "expired":
            raise bad_request("reservation_expired", "Inference reservation has expired")
        if event.status != "reserved":
            raise bad_request("reservation_not_active", "Inference reservation is not active")
        if event.router_client_id != router_client.id:
            raise forbidden("Reservation belongs to a different router")
        if event.api_key_id != db_key.id:
            raise forbidden("Reservation belongs to a different API key")
        expected_raw_tokens = payload.input_tokens + payload.max_output_tokens
        if (
            event.model != payload.model
            or event.prompt_tokens != payload.input_tokens
            or event.raw_total_tokens != expected_raw_tokens
        ):
            raise bad_request("request_id_conflict", "Request id belongs to a different reservation")
        period = await usage_service.current_period(user)
        return self._reservation_response(event, period)

    async def settle_usage(
        self,
        *,
        request: Request,
        router_client: ServiceClient,
        raw_user_api_key: str,
        payload: RouterUsageSettle,
    ) -> RouterReservationResponse:
        db_key, user = await self._authenticate_user_api_key(
            raw_user_api_key,
            redis_client=None,
            apply_rate_limit=False,
        )
        usage_service = UsageService(self.session)
        await usage_service.expire_stale_reservations(user.id)
        event = await self._get_router_event(payload.reservation_id, router_client, db_key, payload.request_id)
        if event.status == "settled" and (
            event.prompt_tokens != payload.input_tokens
            or event.completion_tokens != payload.output_tokens
        ):
            raise bad_request("request_id_conflict", "Request id belongs to different usage")
        already_settled = event.status == "settled"
        event = await usage_service.settle_inference_usage(
            event,
            prompt_tokens=payload.input_tokens,
            completion_tokens=payload.output_tokens,
        )
        if not already_settled:
            await APIKeyService(self.session).record_usage(
                db_key,
                request,
                input_tokens=payload.input_tokens,
                output_tokens=payload.output_tokens,
                request_id=payload.request_id,
            )
        period = await usage_service.current_period(user)
        return self._reservation_response(event, period)

    async def release_reservation(
        self,
        *,
        router_client: ServiceClient,
        raw_user_api_key: str,
        reservation_id: uuid.UUID,
    ) -> RouterReservationResponse:
        db_key, user = await self._authenticate_user_api_key(
            raw_user_api_key,
            redis_client=None,
            apply_rate_limit=False,
        )
        usage_service = UsageService(self.session)
        await usage_service.expire_stale_reservations(user.id)
        event = await self._get_router_event(reservation_id, router_client, db_key, request_id=None)
        if event.status == "settled":
            raise bad_request("reservation_already_settled", "Inference reservation is already settled")
        await usage_service.release_inference_reservation(event)
        period = await usage_service.current_period(user)
        return self._reservation_response(event, period)

    async def _authenticate_user_api_key(
        self,
        raw_user_api_key: str,
        *,
        redis_client: redis.Redis | None,
        apply_rate_limit: bool,
    ) -> tuple[APIKey, User]:
        api_key_service = APIKeyService(self.session)
        db_key = await api_key_service.authenticate_key(
            raw_user_api_key,
            redis_client,
            apply_rate_limit=apply_rate_limit,
        )
        api_key_service.require_scopes(db_key, ["inference:write"])
        user = await self.session.get(User, db_key.user_id)
        if user is None:
            raise unauthorized("API key owner no longer exists")
        if not user.is_active:
            raise forbidden("API key owner is inactive")
        return db_key, user

    async def _get_router_event(
        self,
        reservation_id: uuid.UUID,
        router_client: ServiceClient,
        db_key: APIKey,
        request_id: str | None,
    ) -> InferenceUsageEvent:
        result = await self.session.execute(
            select(InferenceUsageEvent)
            .where(InferenceUsageEvent.id == reservation_id)
            .with_for_update()
        )
        event = result.scalar_one_or_none()
        if event is None:
            raise not_found("Inference reservation not found")
        if request_id is not None and event.request_id != request_id:
            raise forbidden("Reservation request_id mismatch")
        if event.router_client_id != router_client.id:
            raise forbidden("Reservation belongs to a different router")
        if event.api_key_id != db_key.id:
            raise forbidden("Reservation belongs to a different API key")
        return event

    def _reservation_response(
        self,
        event: InferenceUsageEvent,
        period: UsagePeriod,
    ) -> RouterReservationResponse:
        return RouterReservationResponse(
            reservation_id=event.id,
            request_id=event.request_id,
            status=event.status,
            user_id=event.user_id,
            api_key_id=event.api_key_id,
            router_client_id=event.router_client_id,
            model=event.model,
            input_tokens=event.prompt_tokens,
            output_tokens=event.completion_tokens,
            raw_total_tokens=event.raw_total_tokens,
            reserved_tokens=event.reserved_tokens,
            charged_tokens=event.charged_tokens,
            remaining_tokens=period.remaining_tokens,
            expires_at=event.expires_at,
            settled_at=event.settled_at,
            released_at=event.released_at,
        )
