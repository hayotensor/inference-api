"""Create the inference_api SQLite schema for the cross-process E2E.

Run with the inference-api venv and the SAME DATABASE_URL the server uses, BEFORE
starting the uvicorn server, so the tables exist when the app boots. Imports
inference_api.models.Base and runs metadata.create_all against the configured
async engine.
"""

from __future__ import annotations

import asyncio


async def _main() -> None:
    # Importing inference_api.db builds the async engine from settings.database_url
    # (the env we set for the whole E2E). models.Base carries every table.
    from inference_api.db import engine
    from inference_api.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("schema created")


if __name__ == "__main__":
    asyncio.run(_main())
