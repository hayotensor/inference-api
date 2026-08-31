"""Regression tests for agent E's platform-hardening fixes on the serving
plane. See inference_api/main.py for the corresponding changes.

Note: inference_api/config.py is owned by other agents, so the DEBUG+
production guard here is an explicit check inside create_app() rather than
a Settings model_validator (unlike the control plane's app/core/config.py).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from inference_api import main as main_module
from inference_api.config import settings
from inference_api.main import create_app


# --------------------------------------------------------------------- #
# E2 -- CORS loopback equivalence
# --------------------------------------------------------------------- #


async def test_loopback_origin_allowed_as_equivalent_in_non_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/allowlist",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


async def test_untrusted_origin_still_rejected(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/allowlist",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in resp.headers


async def test_cors_stays_strict_in_production_no_loopback_widening(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    monkeypatch.setattr(settings, "debug", False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/allowlist",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in resp.headers


# --------------------------------------------------------------------- #
# E3 -- DEBUG/production guard + generic exception handler
# --------------------------------------------------------------------- #


def test_debug_true_with_production_app_env_fails_fast(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "debug", True)
    with pytest.raises(RuntimeError, match="DEBUG must be false"):
        create_app()


async def test_unhandled_exception_returns_clean_json_not_traceback(monkeypatch):
    """Reproduces the E3 finding on this plane: an unhandled error from
    route logic must come back as a clean JSON envelope, never Starlette's
    HTML traceback page, and the real error is only logged server-side."""
    app = create_app()

    from inference_api import allowlist_service

    async def _boom(self):
        raise RuntimeError("kaboom-from-build-artifact")

    monkeypatch.setattr(allowlist_service.ModelAllowlistService, "build_artifact", _boom)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/allowlist")
        assert resp.status_code == 500
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert body == {"detail": {"code": "internal_error", "message": "An unexpected error occurred"}}
        assert "Traceback" not in resp.text
        assert "kaboom-from-build-artifact" not in resp.text


# --------------------------------------------------------------------- #
# E4 -- security headers, docs gating, public-endpoint throttle
# --------------------------------------------------------------------- #


async def test_security_headers_present_on_response():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "content-security-policy" in resp.headers
        assert "strict-transport-security" not in resp.headers


async def test_docs_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "debug", False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            resp = await client.get(path)
            assert resp.status_code == 404, f"{path} must not be served in production"


async def test_docs_available_outside_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200


async def test_public_endpoint_throttle_returns_429_past_generous_limit(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(main_module, "_PUBLIC_THROTTLE_LIMIT_PER_MINUTE", 3)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        statuses = [
            (await client.get("/allowlist")).status_code for _ in range(5)
        ]
        assert statuses[:3] == [200, 200, 200]
        assert statuses[3:] == [429, 429]

        # A route that isn't in the throttled-path allowlist is unaffected.
        health_statuses = [
            (await client.get("/health")).status_code for _ in range(5)
        ]
        assert health_statuses == [200] * 5


async def test_public_endpoint_throttle_is_off_when_rate_limit_disabled(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(main_module, "_PUBLIC_THROTTLE_LIMIT_PER_MINUTE", 1)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        statuses = [
            (await client.get("/allowlist")).status_code for _ in range(4)
        ]
        assert statuses == [200] * 4
