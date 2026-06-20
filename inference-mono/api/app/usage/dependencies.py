from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_session
from app.usage.service import UsageService


async def get_usage_service(session: AsyncSession = Depends(get_async_session)) -> UsageService:
    return UsageService(session)
