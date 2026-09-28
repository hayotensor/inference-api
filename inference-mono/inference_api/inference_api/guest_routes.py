"""Anonymous "sandbox" inference (HYP-67).

The homepage's ask-box works for a signed-out visitor too: a small, free,
heavily-capped number of real (attested-miner-served) completions per day,
gated by two independent Redis counters (``inference_api.guest_limits``) --
one soft (per browser, via a client-supplied ``X-Guest-Id``), one hard (per
resolved IP, shared across every browser behind it). See that module's
docstring for the full abuse-model writeup.

No API key is required or accepted here. Unlike the authenticated
``/v1/inference`` / ``/v1/chat/completions`` in ``product_routes.py``, this
path never reserves/settles token balance and never touches
``UsageService``/``APIKeyService`` -- there is no user or API key to bill.
It DOES reuse the same miner-selection (``MinerSelector``) and forwarding
(``TEEForwarder``) machinery those routes use, and records the same
``miner_request_stats`` rows via ``stats_recorder`` so a guest-served request
is visible in miner statistics exactly like a paid one -- it is simply never
billed and carries no ``user_id``.

Both the model and the completion-token cap are forced server-side
regardless of what the client sends (``GuestInferenceRequest`` doesn't even
expose those fields) -- see the security note in the HYP-67 ticket: this is
the platform's first anonymous-facing compute endpoint, so nothing from the
caller is trusted beyond the prompt text itself.
"""

from __future__ import annotations

import logging
import uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.client_ip import resolve_client_ip
from inference_api.config import settings
from inference_api.db import get_async_session
from inference_api.errors import bad_request, service_unavailable
from inference_api.guest_limits import check_and_increment_guest_limits
from inference_api.logging import request_id_ctx
from inference_api.pricing import resolve_guest_model_id
from inference_api.product_routes import (
    _attestation_receipt_for,
    _chat_payload_from_prompt,
    _make_forwarder,
    _prompt_too_long_error,
    _text_from_completion,
)
from inference_api import stats_recorder
from inference_api.redis import get_redis
from inference_api.routing.forwarder import ForwardError
from inference_api.routing.selector import MinerSelector
from inference_api.schemas import GuestInferenceRequest, GuestInferenceResponse, InferenceRequest
from inference_api.security import keyed_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/guest", tags=["guest-sandbox"])

_GUEST_LIMIT_MESSAGES = {
    "client_daily_limit_exceeded": (
        "You've used today's free sandbox requests on this browser. Sign up for "
        "an API key for full access."
    ),
    "ip_daily_limit_exceeded": (
        "The free sandbox has hit its shared daily limit for your network. "
        "Sign up for an API key for full access."
    ),
}


async def _require_guest_id(x_guest_id: str | None = Header(default=None, alias="X-Guest-Id")) -> str:
    # Bounded length only (not a strict UUID format check): the header is a
    # soft, client-controlled signal used solely as a Redis-key input, so an
    # unexpected-but-plausible value need not fail the request -- only an
    # absent or absurdly long one (defense against using this as a cheap
    # memory/hash-cost amplifier) does.
    if not x_guest_id or not (8 <= len(x_guest_id) <= 128):
        raise bad_request(
            "guest_id_required",
            "X-Guest-Id header is required (a per-browser id generated once and "
            "stored in localStorage).",
        )
    return x_guest_id


@router.post("/inference", response_model=GuestInferenceResponse)
async def run_guest_inference(
    request: Request,
    payload: GuestInferenceRequest,
    session: AsyncSession = Depends(get_async_session),
    redis_client: redis.Redis = Depends(get_redis),
    guest_id: str = Depends(_require_guest_id),
) -> GuestInferenceResponse:
    if not settings.guest_inference_enabled:
        raise service_unavailable("guest_inference_disabled", "The free sandbox is currently disabled")

    ip = resolve_client_ip(request) or "unknown"

    try:
        limit_result = await check_and_increment_guest_limits(
            redis_client,
            client_id=guest_id,
            ip=ip,
            client_limit=settings.guest_client_daily_limit,
            ip_limit=settings.guest_ip_daily_limit,
        )
    except HTTPException:
        raise
    except Exception:
        # Deliberately FAILS CLOSED, unlike enforce_rate_limit's
        # rate_limit_fail_open default: this is the platform's first
        # anonymous-facing compute surface, so a Redis outage must never
        # silently grant unlimited free inference.
        logger.exception("guest_inference.limiter_unavailable")
        raise service_unavailable(
            "rate_limiter_unavailable", "The free sandbox is temporarily unavailable"
        ) from None

    if not limit_result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": limit_result.reason,
                "message": _GUEST_LIMIT_MESSAGES.get(limit_result.reason, "Rate limit exceeded"),
                "remaining_today": limit_result.client_remaining,
            },
        )

    model_id = await resolve_guest_model_id(session)
    try:
        inference_payload = InferenceRequest(
            model=model_id, prompt=payload.prompt, max_tokens=settings.guest_max_tokens
        )
    except ValidationError as exc:
        raise _prompt_too_long_error(payload.prompt, exc) from exc

    candidates = await MinerSelector(session).select(model_id)
    if not candidates:
        raise service_unavailable(
            "no_capacity", f"The free sandbox model {model_id!r} has no capacity right now"
        )

    forwarder = _make_forwarder(session)
    chat_payload = _chat_payload_from_prompt(inference_payload)
    request_id = request_id_ctx.get() or str(uuid.uuid4())
    last_error: Exception | None = None

    for candidate in candidates[: settings.forward_max_attempts]:
        try:
            result = await forwarder.forward(candidate, chat_payload, request_id=request_id)
        except ForwardError as exc:
            last_error = exc
            await stats_recorder.record_failure(session, candidate, exc, request_id=request_id)
            continue
        usage = result.usage
        output = _text_from_completion(result.body)
        output_tokens = usage.completion_tokens or max(1, len(output.split()))
        prompt_tokens = usage.prompt_tokens or max(1, len(payload.prompt.split()))
        await stats_recorder.record_success(
            session,
            result,
            request_id=request_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=output_tokens,
        )
        await session.commit()
        # Lightweight audit trail: the existing InferenceUsageEvent/APIKeyUsage
        # tables both require a non-null user_id/api_key_id (this is a
        # never-billed, no-user request), so a structured log line -- hashed
        # ip/client_id, never the raw values -- is the observability this path
        # gets instead of a new DB row. See guest_limits.py for the same
        # ip/client hashing used for the rate-limit keys themselves.
        logger.info(
            "guest_inference.served request_id=%s model=%s ip_hash=%s client_hash=%s "
            "prompt_tokens=%s completion_tokens=%s client_count=%s/%s ip_count=%s/%s",
            request_id,
            model_id,
            keyed_hash(ip),
            keyed_hash(guest_id),
            prompt_tokens,
            output_tokens,
            limit_result.client_count,
            limit_result.client_limit,
            limit_result.ip_count,
            limit_result.ip_limit,
        )
        return GuestInferenceResponse(
            id=f"guest_{request_id}",
            model=model_id,
            output=output,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": prompt_tokens + output_tokens,
            },
            attested=True,
            attestation=_attestation_receipt_for(candidate, request_id=request_id, model_id=model_id),
            remaining_today=limit_result.client_remaining,
        )

    # All candidates failed -- persist the failed-attempt rows recorded above.
    await session.commit()
    raise service_unavailable(
        "forward_failed", f"All miners failed for the free sandbox model {model_id!r}: {last_error}"
    )
