"""Public, platform-signed model-allowlist endpoint (WS-F).

``GET /allowlist`` serves the ``ModelAllowlistArtifact`` built from the
``model_allowlist`` table. It is intentionally UNAUTHENTICATED: the body is
Ed25519-signed by the platform key, so any consumer (subnet validators) verifies
it against the PINNED platform pubkey rather than trusting transport auth.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from talaris_contracts import ModelAllowlistArtifact

from inference_api.allowlist_service import ModelAllowlistService
from inference_api.db import get_async_session

router = APIRouter(tags=["allowlist"])


@router.get("/allowlist", response_model=ModelAllowlistArtifact)
async def get_model_allowlist(
    session: AsyncSession = Depends(get_async_session),
) -> ModelAllowlistArtifact:
    service = ModelAllowlistService(session)
    artifact = await service.build_artifact()
    # Persist the idempotent seed so the signed version stays stable across reads.
    await session.commit()
    return artifact
