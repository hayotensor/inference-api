from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from inference_api.allowlist_routes import router as allowlist_router
from inference_api.config import settings
from inference_api.logging import configure_logging, request_id_ctx
from inference_api.maintenance.tasks import MaintenanceLoop
from inference_api.miners.routes import router as miners_router
from inference_api.product_routes import router as product_router
from inference_api.provisioner.loop import ProvisionerLoop
from inference_api.redis import close_redis, get_redis_client
from inference_api.router_routes import router as router_service_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.debug)
    provisioner_loop: ProvisionerLoop | None = None
    maintenance_loop: MaintenanceLoop | None = None
    if settings.provisioner_enabled:
        redis_client = None
        try:
            redis_client = get_redis_client()
        except Exception:  # noqa: BLE001 - redis optional; loops fall back to asyncio locks
            redis_client = None
        provisioner_loop = ProvisionerLoop(redis_client=redis_client)
        maintenance_loop = MaintenanceLoop()
        provisioner_loop.start()
        maintenance_loop.start()
    try:
        yield
    finally:
        if provisioner_loop is not None:
            await provisioner_loop.stop()
        if maintenance_loop is not None:
            await maintenance_loop.stop()
        await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-User-API-Key",
            settings.request_id_header,
        ],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get(settings.request_id_header) or str(uuid.uuid4())
        token = request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
            response.headers[settings.request_id_header] = request_id
            return response
        finally:
            request_id_ctx.reset(token)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(product_router)
    app.include_router(router_service_router)
    app.include_router(miners_router)
    app.include_router(allowlist_router)
    return app


app = create_app()
