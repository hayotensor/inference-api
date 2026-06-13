import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api_keys.routes import router as api_keys_router
from app.auth.routes import router as auth_router
from app.core.config import settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.rate_limit import limiter
from app.core.redis import close_redis
from app.product_api.routes import router as product_router
from app.users.routes import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.debug)
    yield
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret.get_secret_value(),
        https_only=settings.app_env == "production",
        same_site="lax",
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", settings.request_id_header],
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

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(api_keys_router)
    app.include_router(product_router)
    return app


app = create_app()
