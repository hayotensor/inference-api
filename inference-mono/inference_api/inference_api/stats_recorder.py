"""Record every forward attempt as a ``miner_request_stats`` row.

This is the entry point of the miner-statistics pipeline and the platform's only
first-hand measurement of miner behavior. It replaces the removed merkle-DAG
plane, where miners self-reported the requests they had handled: the platform
selects the miner and forwards the request, so it already knows the truth and does
not need — or want — the miner to broadcast it.

Two rules shape this module:

**Never break the request path.** Statistics are valuable but a user's inference is
more valuable. ``record_attempt`` swallows every exception and rolls nothing back;
losing a stats row is an acceptable outcome, failing a paid request is not.

**Record failures too.** The old failover loop discarded ``ForwardError``s, so a
miner that accepted routing and then failed looked identical to one that was never
tried. That asymmetry is exactly what made self-reported statistics necessary, so
every attempt — success, timeout, HTTP error, pin mismatch — gets a row.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from inference_api.chain import ChainClient
from inference_api.models import MinerRequestStat
from inference_api.routing.forwarder import (
    OUTCOME_SUCCESS,
    ForwardError,
    ForwardResult,
    StreamForward,
)
from inference_api.routing.selector import MinerCandidate

logger = logging.getLogger(__name__)


# Process-wide chain client, reused so the epoch-length constant and the
# short-lived current-epoch cache are shared by every request. Swapped by
# ``set_chain_client`` in tests and by the E2E harness (mirrors the
# ``product_routes.set_forwarder_transport`` seam).
_chain_client: ChainClient | None = None


def set_chain_client(client: ChainClient | None) -> None:
    """Install (or clear) the chain client used to stamp epochs."""
    global _chain_client
    _chain_client = client


def get_chain_client() -> ChainClient:
    global _chain_client
    if _chain_client is None:
        _chain_client = ChainClient()
    return _chain_client


def current_epoch() -> int | None:
    """The subnet epoch to stamp on a new row, or ``None`` if unavailable.

    ``None`` is not an error: an unstamped row simply cannot be aggregated into an
    epoch snapshot. Failing the request instead would let a chain RPC outage take
    down inference.
    """
    try:
        return get_chain_client().current_epoch()
    except Exception:  # noqa: BLE001 - epoch stamping must never break a request
        logger.debug("stats: current epoch unavailable", exc_info=True)
        return None


async def record_attempt(
    session: AsyncSession,
    candidate: MinerCandidate,
    *,
    outcome: str,
    request_id: str | None = None,
    http_status: int | None = None,
    latency_ms: int | None = None,
    ttft_ms: int | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    epoch: int | None = None,
) -> MinerRequestStat | None:
    """Append one attempt row. Never raises; returns the row, or None if skipped.

    The row is added to ``session`` but NOT committed — it rides along with the
    caller's existing commit so a successful request and its statistics land
    atomically. Callers on a failure path that does not otherwise commit must
    commit themselves.
    """
    try:
        row = MinerRequestStat(
            epoch=current_epoch() if epoch is None else epoch,
            miner_id=candidate.miner_id,
            miner_hotkey=candidate.hotkey,
            subnet_node_id=candidate.subnet_node_id,
            miner_model_id=candidate.miner_model_id,
            model_id=candidate.model_id,
            model_version=candidate.model_version,
            model_hash=candidate.model_hash,
            request_id=request_id,
            outcome=outcome,
            http_status=http_status,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            prompt_tokens=max(0, int(prompt_tokens or 0)),
            completion_tokens=max(0, int(completion_tokens or 0)),
            total_tokens=max(0, int(total_tokens or 0)),
        )
        session.add(row)
        return row
    except Exception:  # noqa: BLE001 - statistics must never break the request path
        logger.warning(
            "stats: failed to record attempt for hotkey=%s model=%s outcome=%s",
            getattr(candidate, "hotkey", None),
            getattr(candidate, "model_id", None),
            outcome,
            exc_info=True,
        )
        return None


async def record_success(
    session: AsyncSession,
    result: ForwardResult,
    *,
    request_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> MinerRequestStat | None:
    """Record a successful non-streaming forward.

    ``prompt_tokens``/``completion_tokens`` override the enclave-reported counts
    with whatever the caller actually settled on, so the statistics row and the
    billing row can never disagree about the same request.
    """
    usage = result.usage
    prompt = usage.prompt_tokens if prompt_tokens is None else prompt_tokens
    completion = usage.completion_tokens if completion_tokens is None else completion_tokens
    return await record_attempt(
        session,
        result.candidate,
        outcome=OUTCOME_SUCCESS,
        request_id=request_id,
        http_status=result.status_code,
        latency_ms=result.latency_ms,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=usage.total_tokens or (prompt + completion),
    )


async def record_stream_success(
    session: AsyncSession,
    stream: StreamForward,
    *,
    request_id: str | None = None,
) -> MinerRequestStat | None:
    """Record a completed streaming forward (call after the stream is closed)."""
    usage = stream.usage
    return await record_attempt(
        session,
        stream.candidate,
        outcome=OUTCOME_SUCCESS,
        request_id=request_id,
        http_status=200,
        latency_ms=stream.latency_ms,
        ttft_ms=stream.ttft_ms,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens or (usage.prompt_tokens + usage.completion_tokens),
    )


async def record_failure(
    session: AsyncSession,
    candidate: MinerCandidate,
    error: BaseException,
    *,
    request_id: str | None = None,
) -> MinerRequestStat | None:
    """Record a failed attempt, taking the outcome/timing off the ``ForwardError``.

    A non-``ForwardError`` exception still gets a row (with the generic outcome and
    no timing) rather than being dropped — an unexplained failure against a miner is
    still a failure against that miner.
    """
    outcome = getattr(error, "outcome", None) or "forward_error"
    return await record_attempt(
        session,
        candidate,
        outcome=str(outcome),
        request_id=request_id,
        http_status=getattr(error, "http_status", None) if isinstance(error, ForwardError) else None,
        latency_ms=getattr(error, "latency_ms", None) if isinstance(error, ForwardError) else None,
    )


__all__ = [
    "current_epoch",
    "get_chain_client",
    "record_attempt",
    "record_failure",
    "record_stream_success",
    "record_success",
    "set_chain_client",
]
