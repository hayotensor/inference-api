import logging
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin.routes import router as admin_router
from app.api_keys.routes import router as api_keys_router
from app.auth.routes import router as auth_router
from app.billing.routes import router as billing_router
from app.core.config import settings
from app.core.logging import configure_logging, request_id_ctx
from app.core.rate_limit import ClientIPMiddleware, limiter
from app.core.redis import close_redis
from app.internal.routes import router as internal_router
from app.usage.routes import router as usage_router
from app.users.routes import router as users_router
from app.wallets.routes import router as wallets_router

logger = logging.getLogger(__name__)

_LOOPBACK_HOST_SPELLINGS = ("localhost", "127.0.0.1", "[::1]")
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _expand_loopback_origins(origins: list[str]) -> list[str]:
    """Treat localhost/127.0.0.1/[::1] as equivalent CORS origins.

    Only ever applied in non-production environments (see create_app): a
    dashboard reachable at any loopback spelling on the configured port is
    allowed without requiring three separate CORS_ORIGINS entries in an
    env file we cannot edit. Non-loopback origins are returned unchanged, so
    this never widens trust beyond what was already explicitly configured.
    """
    expanded = dict.fromkeys(origins)
    for origin in origins:
        parts = urlsplit(origin)
        if parts.hostname not in _LOOPBACK_HOSTNAMES:
            continue
        port_part = f":{parts.port}" if parts.port else ""
        for host in _LOOPBACK_HOST_SPELLINGS:
            expanded.setdefault(f"{parts.scheme}://{host}{port_part}", None)
    return list(expanded)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.debug)
    if settings.app_env == "production" and not settings.trusted_proxy_ips:
        # Not a hard failure: plenty of production topologies have no
        # reverse proxy in front at all, in which case this is correct.
        # But if one *is* present (e.g. the cloudflared tunnel in
        # docker-compose.production.yml), leaving this unset means
        # X-Forwarded-For is never trusted and every request will be keyed
        # on the proxy's own IP -- see app/core/rate_limit.py.
        logger.warning(
            "trusted_proxy_ips is empty in production; if a reverse proxy "
            "fronts this service, set TRUSTED_PROXY_IPS or every client will "
            "share one rate-limit bucket and one audit-log IP"
        )
    if settings.auto_verify_new_users:
        # Loud on every boot, in every environment: this makes "verified email"
        # meaningless, so it must never be on by accident.
        logger.warning(
            "AUTO_VERIFY_NEW_USERS is enabled: new accounts are marked "
            "email-verified without any verification email being sent. This is "
            "a development convenience and must NOT be used in production."
        )
    yield
    await close_redis()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Never let FastAPI/Starlette render its debug traceback page to an
        # HTTP client, regardless of settings.debug (which still controls log
        # verbosity via configure_logging above). Production additionally
        # fails startup outright if DEBUG=true -- see
        # Settings.validate_production_secrets -- but this is hardcoded
        # false so a raw traceback can never leak to a caller in ANY
        # environment; the generic_exception_handler below logs it
        # server-side instead.
        debug=False,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # HTTPException (and RateLimitExceeded) are matched by their own,
        # more specific handlers before Starlette ever falls through to this
        # one -- see starlette._exception_handler._lookup_exception_handler,
        # which walks the exception's MRO -- so this only ever fires for
        # truly unhandled errors, and never masks a structured error-helper
        # response from app/core/errors.py.
        logger.exception(
            "unhandled_exception",
            extra={"path": request.url.path, "method": request.method},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "internal_error", "message": "An unexpected error occurred"}},
        )

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
    cors_origins = (
        settings.cors_origins
        if settings.app_env == "production"
        else _expand_loopback_origins(settings.cors_origins)
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-User-API-Key",
            settings.request_id_header,
        ],
    )
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        # HSTS only makes sense once we know the request was actually served
        # over HTTPS. `request.url.scheme` reflects `scope["scheme"]`, which
        # uvicorn's ProxyHeadersMiddleware only sets from X-Forwarded-Proto
        # for peers it trusts -- the same FORWARDED_ALLOW_IPS configuration
        # discussed for X-Forwarded-For in app/core/rate_limit.py, so this
        # header will only appear in production once that is set correctly.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response

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

    # Outermost middleware (added last -- see Starlette's build_middleware_stack,
    # which nests each add_middleware call around all previously-registered
    # ones): resolves the trustworthy client IP before CORS/TrustedHost/
    # session/rate-limit or any route handler runs. See ClientIPMiddleware's
    # docstring for the uvicorn forwarded-headers interaction this depends on.
    app.add_middleware(ClientIPMiddleware)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(api_keys_router)
    app.include_router(billing_router)
    app.include_router(wallets_router)
    app.include_router(usage_router)
    app.include_router(internal_router)
    return app


app = create_app()
