"""Pull each enclave's signed usage-receipt chain and reconcile it against our own.

This replaces the miner-side usage-batch collector that used to publish receipts
onto the merkle-DAG. The logic barely changed; the ACTOR did. The platform holds
the enclave admin token, already talks to every enclave, and knows which requests
it dispatched — so it pulls the receipts itself and the miner never broadcasts
anything about the requests it handled.

WHAT RECONCILIATION BUYS
------------------------
Scoring uses ``verified_*`` token counts only, and a token count is verified only
when it appears in BOTH sources:

* the platform's own ``miner_request_stats`` row (written when it forwarded), and
* the enclave's hash-chained, Ed25519-signed usage row for the same ``request_id``,
  with matching token counts.

Neither party can inflate a reward alone. A platform bug that double-counts a
request finds no matching receipt; a miner that fabricates receipts finds no
matching dispatch. Anything that fails to corroborate simply stays unverified —
never rejected loudly, never counted.

FAIL-CLOSED, NEVER FATAL
------------------------
Every check that cannot be completed (no attested verify key, bad signature, a
non-monotonic usage counter) results in NOT verifying rows and NOT advancing the
cursor, so the same rows are retried next pass. Every failure is caught and logged:
one unreachable enclave must not stop the maintenance pass for all the others.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from talaris_contracts import usage_head_signing_message

from inference_api.config import settings
from inference_api.crypto import decrypt_token
from inference_api.models import Miner, MinerModel, MinerRequestStat, ProvisionedToken
from inference_api.routing.pinning import PinError, build_enclave_client

logger = logging.getLogger(__name__)

RECEIPTS_PATH = "/usage/receipts"

# Stable skip reasons (logged, and returned for tests/observability).
SKIP_NO_ADMIN_TOKEN = "no_admin_token"
SKIP_NO_ENDPOINT = "no_endpoint"
SKIP_NO_VERIFY_KEY = "no_attested_verify_key"
SKIP_VERIFY_KEY_MISMATCH = "verify_key_mismatch"
SKIP_BAD_HEAD_SIGNATURE = "bad_head_signature"
SKIP_NO_HEAD_SIGNATURE = "no_head_signature"
SKIP_USAGE_ROLLBACK = "usage_rollback"
SKIP_FETCH_FAILED = "fetch_failed"
SKIP_MALFORMED = "malformed_response"


@dataclass
class ModelReceiptsResult:
    """Outcome of one (miner, model) receipts pull."""

    miner_hotkey: str
    model_id: str
    rows_fetched: int = 0
    rows_verified: int = 0
    rows_unmatched: int = 0
    rows_truncated: int = 0
    cursor_advanced_to: int | None = None
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        return self.skipped is None


@dataclass
class ReceiptsSummary:
    results: list[ModelReceiptsResult] = field(default_factory=list)

    @property
    def rows_verified(self) -> int:
        return sum(r.rows_verified for r in self.results)

    @property
    def models_ok(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def models_skipped(self) -> int:
        return sum(1 for r in self.results if not r.ok)


def _verify_head_signature(
    *, usage_count: int, usage_chain_head: str, signature_hex: str, verify_key_hex: str
) -> bool:
    """Check the enclave's Ed25519 signature over its cumulative usage head.

    ``epoch`` is ``None`` in the signed message because the feed is paged by
    ``since_row_id`` only — the enclave signs whatever ``since_epoch`` it was given,
    and we never pass one. Returns False rather than raising on any malformed input.
    """
    try:
        message = usage_head_signing_message(usage_count, usage_chain_head, None)
        VerifyKey(bytes.fromhex(verify_key_hex)).verify(message, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


class TeeReceiptsCollector:
    """Pull + reconcile the enclave receipt chain for every attested miner model."""

    def __init__(
        self, session: AsyncSession, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.session = session
        # Injected transport (tests / E2E): bypasses TLS pinning, exactly as the
        # forwarder's test seam does.
        self._transport = transport

    # ------------------------------------------------------------------ public

    async def collect_all(self) -> ReceiptsSummary:
        """Pull receipts for every attested, loaded model. Never raises."""
        summary = ReceiptsSummary()
        try:
            targets = await self._targets()
        except Exception:  # noqa: BLE001 - a broken query must not kill the pass
            logger.warning("receipts: failed to enumerate targets", exc_info=True)
            return summary

        for miner, model in targets:
            try:
                summary.results.append(await self.collect_model(miner, model))
            except Exception:  # noqa: BLE001 - one bad enclave must not stop the rest
                logger.warning(
                    "receipts: unhandled error for hotkey=%s model=%s",
                    miner.hotkey,
                    model.model_id,
                    exc_info=True,
                )
                summary.results.append(
                    ModelReceiptsResult(
                        miner_hotkey=miner.hotkey,
                        model_id=model.model_id,
                        skipped=SKIP_FETCH_FAILED,
                    )
                )

        logger.info(
            "receipts: models_ok=%s models_skipped=%s rows_verified=%s",
            summary.models_ok,
            summary.models_skipped,
            summary.rows_verified,
        )
        return summary

    async def collect_model(self, miner: Miner, model: MinerModel) -> ModelReceiptsResult:
        """Pull + reconcile one (miner, model) enclave receipt page."""
        result = ModelReceiptsResult(miner_hotkey=miner.hotkey, model_id=model.model_id)

        endpoint = model.tee_endpoint or miner.tee_endpoint
        if not endpoint:
            return self._skip(result, SKIP_NO_ENDPOINT)

        admin_token = await self._admin_token(miner.id, model.model_id)
        if not admin_token:
            # Without the admin bearer there is no authorized way to read the feed.
            # (The bound-miner signature path belongs to the miner, not to us.)
            return self._skip(result, SKIP_NO_ADMIN_TOKEN)

        # The trust anchor: only the key captured during ATTESTATION may sign this
        # enclave's head. Accepting the key the response carries would let any host
        # sign its own numbers.
        attested_key = model.enclave_verify_key or miner.enclave_verify_key
        if not attested_key:
            return self._skip(result, SKIP_NO_VERIFY_KEY)

        payload = await self._fetch(
            endpoint=endpoint,
            fingerprint=model.tls_cert_fingerprint or miner.tls_cert_fingerprint,
            admin_token=admin_token,
            cursor=model.receipt_cursor_row_id,
        )
        if payload is None:
            return self._skip(result, SKIP_FETCH_FAILED)
        if not isinstance(payload, dict):
            return self._skip(result, SKIP_MALFORMED)

        signature = payload.get("head_signature")
        reported_key = payload.get("verify_key")
        if not signature or not reported_key:
            # The enclave omits these only when it has no identity yet; there is
            # nothing to anchor the rows to, so leave them unverified.
            return self._skip(result, SKIP_NO_HEAD_SIGNATURE)
        if str(reported_key).lower() != str(attested_key).lower():
            return self._skip(result, SKIP_VERIFY_KEY_MISMATCH)

        try:
            usage_count = int(payload["usage_count"])
            usage_chain_head = str(payload.get("usage_chain_head") or "")
        except (KeyError, TypeError, ValueError):
            return self._skip(result, SKIP_MALFORMED)

        if not _verify_head_signature(
            usage_count=usage_count,
            usage_chain_head=usage_chain_head,
            signature_hex=str(signature),
            verify_key_hex=str(attested_key),
        ):
            return self._skip(result, SKIP_BAD_HEAD_SIGNATURE)

        # Anti-replay: the cumulative counter can only ever go up. A decrease means
        # a rolled-back or substituted enclave store, so trust nothing from this pull.
        if model.usage_count is not None and usage_count < int(model.usage_count):
            logger.warning(
                "receipts: usage rollback for hotkey=%s model=%s (%s -> %s); refusing rows",
                miner.hotkey,
                model.model_id,
                model.usage_count,
                usage_count,
            )
            return self._skip(result, SKIP_USAGE_ROLLBACK)

        rows = payload.get("rows") or []
        if not isinstance(rows, Sequence):
            return self._skip(result, SKIP_MALFORMED)

        limit = int(settings.tee_receipts_page_limit)
        if len(rows) > limit:
            # Truncation is REPORTED, never silent: the cursor advances only over the
            # rows actually processed, so the remainder is picked up next pass.
            result.rows_truncated = len(rows) - limit
            logger.info(
                "receipts: truncating %s of %s rows for hotkey=%s model=%s (limit=%s)",
                result.rows_truncated,
                len(rows),
                miner.hotkey,
                model.model_id,
                limit,
            )
            rows = list(rows)[:limit]

        result.rows_fetched = len(rows)
        verified, unmatched, max_row_id = await self._reconcile(miner, model, rows)
        result.rows_verified = verified
        result.rows_unmatched = unmatched

        # Advance the durable cursor and refresh the cumulative anchor. Only reached
        # once the head signature verified, so these values are enclave-attested.
        if max_row_id is not None:
            model.receipt_cursor_row_id = max_row_id
            result.cursor_advanced_to = max_row_id
        model.usage_count = usage_count
        model.usage_chain_head = usage_chain_head
        self.session.add(model)

        logger.debug(
            "receipts: hotkey=%s model=%s fetched=%s verified=%s unmatched=%s cursor=%s",
            miner.hotkey,
            model.model_id,
            result.rows_fetched,
            result.rows_verified,
            result.rows_unmatched,
            result.cursor_advanced_to,
        )
        return result

    # ---------------------------------------------------------------- internal

    def _skip(self, result: ModelReceiptsResult, reason: str) -> ModelReceiptsResult:
        result.skipped = reason
        logger.info(
            "receipts: skipping hotkey=%s model=%s reason=%s",
            result.miner_hotkey,
            result.model_id,
            reason,
        )
        return result

    async def _targets(self) -> list[tuple[Miner, MinerModel]]:
        """Attested, loaded models — the same gate the router uses to select."""
        result = await self.session.execute(
            select(Miner, MinerModel)
            .join(MinerModel, MinerModel.miner_id == Miner.id)
            .where(
                MinerModel.loaded.is_(True),
                MinerModel.attestation_status == "attested",
            )
        )
        return [(miner, model) for miner, model in result.all()]

    async def _admin_token(self, miner_id, model_id: str) -> str | None:
        """Decrypt the active enclave admin bearer for this (miner, model)."""
        row = (
            await self.session.execute(
                select(ProvisionedToken).where(
                    ProvisionedToken.miner_id == miner_id,
                    ProvisionedToken.model_id == model_id,
                    ProvisionedToken.status == "active",
                )
            )
        ).scalars().first()
        if row is None or not row.admin_encrypted_token:
            return None
        try:
            return decrypt_token(row.admin_encrypted_token)
        except Exception:  # noqa: BLE001 - a undecryptable token is a skip, not a crash
            logger.warning("receipts: could not decrypt admin token for miner=%s", miner_id, exc_info=True)
            return None

    async def _fetch(
        self,
        *,
        endpoint: str,
        fingerprint: str | None,
        admin_token: str,
        cursor: int | None,
    ) -> dict | None:
        url = RECEIPTS_PATH if cursor is None else f"{RECEIPTS_PATH}?since_row_id={int(cursor)}"
        try:
            client = build_enclave_client(
                base_url=endpoint,
                tls_cert_fingerprint=fingerprint,
                bearer=admin_token,
                transport=self._transport,
                timeout=httpx.Timeout(
                    settings.tee_receipts_timeout_seconds,
                    connect=settings.tee_connect_timeout_seconds,
                ),
            )
        except PinError:
            logger.warning("receipts: no attested TLS fingerprint for %s", endpoint, exc_info=True)
            return None

        try:
            response = await client.get(url, headers={"Authorization": f"Bearer {admin_token}"})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("receipts: fetch failed for %s%s", endpoint, url, exc_info=True)
            return None
        finally:
            await client.aclose()

    async def _reconcile(
        self, miner: Miner, model: MinerModel, rows: Sequence[dict]
    ) -> tuple[int, int, int | None]:
        """Mark matching attempt rows verified. Returns (verified, unmatched, max_row_id).

        A receipt matches an attempt when the ``request_id`` and all three token
        counts agree. A receipt with no matching attempt is COUNTED, not trusted —
        it cannot contribute to a reward, and a persistent count is the signal that
        something is wrong with dispatch bookkeeping.
        """
        verified = 0
        unmatched = 0
        max_row_id: int | None = None

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = _maybe_int(row.get("row_id"))
            if row_id is not None:
                max_row_id = row_id if max_row_id is None else max(max_row_id, row_id)

            request_id = row.get("request_id")
            if not request_id:
                # A pre-``request_id`` (v0) enclave row is not individually
                # addressable, so it can never be matched to a dispatch.
                unmatched += 1
                continue

            attempt = (
                await self.session.execute(
                    select(MinerRequestStat).where(
                        MinerRequestStat.miner_id == miner.id,
                        MinerRequestStat.request_id == str(request_id),
                        MinerRequestStat.model_id == model.model_id,
                    )
                )
            ).scalars().first()
            if attempt is None:
                unmatched += 1
                continue

            if not _tokens_match(attempt, row):
                logger.info(
                    "receipts: token mismatch for request_id=%s hotkey=%s "
                    "(platform %s/%s/%s vs enclave %s/%s/%s); leaving unverified",
                    request_id,
                    miner.hotkey,
                    attempt.prompt_tokens,
                    attempt.completion_tokens,
                    attempt.total_tokens,
                    row.get("prompt_tokens"),
                    row.get("completion_tokens"),
                    row.get("total_tokens"),
                )
                unmatched += 1
                continue

            if not attempt.verified:
                attempt.verified = True
                self.session.add(attempt)
            verified += 1

        return verified, unmatched, max_row_id


def _maybe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tokens_match(attempt: MinerRequestStat, row: dict) -> bool:
    """All three counts must agree; a missing enclave count never counts as a match."""
    for attr, key in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        reported = _maybe_int(row.get(key))
        if reported is None or reported != int(getattr(attempt, attr) or 0):
            return False
    return True


__all__ = [
    "RECEIPTS_PATH",
    "ModelReceiptsResult",
    "ReceiptsSummary",
    "TeeReceiptsCollector",
]
