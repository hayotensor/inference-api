from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from inference_api.allowlist_routes import router as allowlist_router
from inference_api.attest_routes import router as attest_router
from inference_api.config import settings
from inference_api.e2ee_routes import E2EE_REQUEST_HEADERS, E2EE_RESPONSE_HEADERS
from inference_api.e2ee_routes import router as e2ee_router
from inference_api.logging import configure_logging, request_id_ctx
from inference_api.maintenance.tasks import MaintenanceLoop
from inference_api.miners.routes import router as miners_router
from inference_api.product_routes import router as product_router
from inference_api.provisioner.loop import ProvisionerLoop
from inference_api.redis import close_redis, get_redis_client
from inference_api.router_routes import router as router_service_router
from inference_api.stats_routes import router as miner_stats_router
from inference_api.status_routes import router as status_router

logger = logging.getLogger(__name__)

_LOOPBACK_HOST_SPELLINGS = ("localhost", "127.0.0.1", "[::1]")
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _expand_loopback_origins(origins: list[str]) -> list[str]:
    """Treat localhost/127.0.0.1/[::1] as equivalent CORS origins.

    Mirrors app/main.py (the control plane) -- see that copy for the full
    rationale. Only ever applied in non-production environments; production
    uses settings.cors_origins verbatim.
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


# --- Abuse-throttle for unauthenticated public endpoints (E4) ------------- #
# /allowlist, /attest, and /subnet/stats/* are intentionally unauthenticated
# (see their own docstrings), and /docs, /redoc, /openapi.json carry no auth
# either. There is no per-route rate limiter on this plane at all (unlike the
# control plane's SlowAPI-based one), so give these a generous, dependency-free,
# in-process throttle keyed on client IP. Deliberately reuses only settings
# that already exist on this plane (rate_limit_enabled / rate_limit_fail_open)
# -- no new config surface is added to inference_api/config.py.
_PUBLIC_THROTTLE_PATH_PREFIXES: tuple[str, ...] = (
    "/allowlist",
    "/attest",
    "/subnet/stats",
    "/status",
    "/docs",
    "/redoc",
    "/openapi.json",
)
_PUBLIC_THROTTLE_LIMIT_PER_MINUTE = 120
_public_throttle_counters: dict[tuple[str, int], int] = {}
_public_throttle_lock = asyncio.Lock()


def _is_public_throttled_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_THROTTLE_PATH_PREFIXES)


async def _public_endpoint_throttle_middleware(request: Request, call_next):
    if not settings.rate_limit_enabled or not _is_public_throttled_path(request.url.path):
        return await call_next(request)
    client_host = request.client.host if request.client else None
    if not client_host:
        return await call_next(request)
    try:
        bucket = int(time.time() // 60)
        key = (client_host, bucket)
        async with _public_throttle_lock:
            stale = [existing for existing in _public_throttle_counters if existing[1] < bucket]
            for existing in stale:
                del _public_throttle_counters[existing]
            count = _public_throttle_counters.get(key, 0) + 1
            _public_throttle_counters[key] = count
    except Exception:
        logger.exception("public_endpoint_throttle.error")
        if not settings.rate_limit_fail_open:
            return JSONResponse(
                status_code=503,
                content={"detail": {"code": "rate_limit_unavailable", "message": "Rate limiter unavailable"}},
            )
        return await call_next(request)
    if count > _PUBLIC_THROTTLE_LIMIT_PER_MINUTE:
        return JSONResponse(
            status_code=429,
            content={"detail": {"code": "rate_limit_exceeded", "message": "Too many requests"}},
        )
    return await call_next(request)


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
        provisioner_loop.start()
    # The maintenance loop also drives the subnet-stake allowance refresh, so it
    # must run whenever either the provisioner or the stake quota is enabled.
    if settings.provisioner_enabled or settings.subnet_stake_quota_enabled:
        maintenance_loop = MaintenanceLoop()
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
    # inference_api/config.py is out of scope for this fix (owned elsewhere),
    # so it has no equivalent to the control plane's
    # Settings.validate_production_secrets DEBUG guard. Enforce it here
    # instead, at app-creation time, so the combination still fails fast.
    if settings.app_env == "production" and settings.debug:
        raise RuntimeError("DEBUG must be false when APP_ENV=production")

    docs_enabled = settings.app_env != "production"
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        # Never render Starlette's debug traceback page to an HTTP client;
        # settings.debug still controls log verbosity via configure_logging
        # above. See generic_exception_handler below for the client-facing
        # replacement (clean JSON + server-side logging).
        debug=False,
        # /docs, /redoc, /openapi.json are not authenticated and, per this
        # fix, are also unthrottled by definition once disabled -- simplest
        # to just not serve them in production at all.
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # HTTPException gets its own, more specific handler earlier in
        # Starlette's MRO-based lookup (see
        # starlette._exception_handler._lookup_exception_handler), so this
        # only fires for genuinely unhandled errors and never masks a
        # structured error-helper response from inference_api/errors.py.
        logger.exception(
            "unhandled_exception",
            extra={"path": request.url.path, "method": request.method},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "internal_error", "message": "An unexpected error occurred"}},
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
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-User-API-Key",
            settings.request_id_header,
            # talaris-e2ee/v1 routing metadata (POST /v1/e2e/invoke).
            *E2EE_REQUEST_HEADERS,
        ],
        # A browser client must be able to read the E2EE response metadata.
        expose_headers=[*E2EE_RESPONSE_HEADERS, settings.request_id_header],
    )

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path not in {"/docs", "/redoc"}:
            # Skip on the docs pages themselves (only served outside
            # production anyway) so Swagger/ReDoc's own CDN-loaded assets
            # keep working; every JSON endpoint gets the strict policy.
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        # HSTS only makes sense once we know the request was actually served
        # over HTTPS. `request.url.scheme` reflects `scope["scheme"]`, which
        # uvicorn's ProxyHeadersMiddleware only sets from X-Forwarded-Proto
        # for peers it trusts (its --forwarded-allow-ips/FORWARDED_ALLOW_IPS),
        # so this header only appears in production once that is configured.
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response

    app.middleware("http")(_public_endpoint_throttle_middleware)

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
    app.include_router(e2ee_router)
    app.include_router(router_service_router)
    app.include_router(miners_router)
    app.include_router(allowlist_router)
    app.include_router(attest_router)
    app.include_router(miner_stats_router)
    app.include_router(status_router)
    return app


app = create_app()
