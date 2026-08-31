"""Regression tests for the E1-E4 platform-hardening fixes.

Ownership: agent E. See app/main.py, app/core/rate_limit.py,
app/core/config.py for the corresponding fixes.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, settings
from app.core.rate_limit import limiter, resolve_client_ip
from app.db.models.audit_log import AuditLog
from app.db.session import async_session_maker
from app.main import create_app


# --------------------------------------------------------------------- #
# E1 -- client IP resolution (unit-level)
# --------------------------------------------------------------------- #


def test_resolve_client_ip_ignores_untrusted_forwarded_header(monkeypatch):
    """Safe default: with no configured trusted proxies, a client-supplied
    X-Forwarded-For header must never override the real socket peer.

    Fails against the old code: the old key_func (slowapi's bare
    get_remote_address) had no such check at all -- it just returned
    whatever request.client.host happened to be, so this function did not
    exist to call. This test targets the new resolve_client_ip directly.
    """
    monkeypatch.setattr(settings, "trusted_proxy_ips", [])
    resolved = resolve_client_ip("198.51.100.7", "203.0.113.5")
    assert resolved == "198.51.100.7"


def test_resolve_client_ip_trusts_configured_proxy_and_extracts_real_client(monkeypatch):
    """The mirror-image fix: once a hop is explicitly configured as trusted,
    the real client is extracted from X-Forwarded-For instead of collapsing
    every caller onto the proxy's own IP."""
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["172.19.0.5"])
    resolved = resolve_client_ip("172.19.0.5", "198.51.100.42")
    assert resolved == "198.51.100.42"
    # An untrusted peer is still not honored, even with proxies configured.
    resolved_untrusted = resolve_client_ip("203.0.113.9", "198.51.100.42")
    assert resolved_untrusted == "203.0.113.9"


# --------------------------------------------------------------------- #
# E1 -- end-to-end: spoofed header vs. audit log / limiter agreement
# --------------------------------------------------------------------- #


async def test_spoofed_forwarded_for_does_not_poison_audit_log(monkeypatch):
    """app/audit/service.py's own _client_ip() helper reads
    request.headers['x-forwarded-for'] directly and, on the old code path,
    trusted it unconditionally. ClientIPMiddleware strips that header from
    the scope once it has made its own (safe-by-default) trust decision, so
    the audit row must show the real peer, never the attacker-declared one.

    Confirmed this fails without the fix by checking app/main.py's git diff:
    the old app never registered ClientIPMiddleware, so the raw
    X-Forwarded-For header reached app/audit/service.py untouched and the
    audit row would have stored "203.0.113.66" instead of the ASGI test
    peer.
    """
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/auth/register",
            json={"email": "spoof-audit@example.com", "password": "correct-horse-123"},
            headers={"X-Forwarded-For": "203.0.113.66"},
        )
        assert resp.status_code == 201, resp.text

    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "auth.register")
            )
        ).scalars().first()
        assert row is not None
        assert row.ip_address != "203.0.113.66"
        # httpx's ASGITransport default peer, unaffected by the header.
        assert row.ip_address == "127.0.0.1"


async def test_trusted_proxy_ip_yields_real_client_in_audit_log_not_proxy_ip(monkeypatch):
    """With a trusted proxy configured, the *proxy's* IP (the ASGI peer)
    must NOT be what lands in the audit trail -- the declared real client
    should, and it must differ between two different end users funneled
    through the same proxy (the "mirror image" bug: today, every audit row
    behind a real reverse proxy shows the same proxy IP)."""
    monkeypatch.setattr(settings, "trusted_proxy_ips", ["172.19.0.5"])
    app = create_app()
    transport = ASGITransport(app=app, client=("172.19.0.5", 40000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        r1 = await client.post(
            "/auth/register",
            json={"email": "real-client-a@example.com", "password": "correct-horse-123"},
            headers={"X-Forwarded-For": "198.51.100.10"},
        )
        assert r1.status_code == 201, r1.text
        r2 = await client.post(
            "/auth/register",
            json={"email": "real-client-b@example.com", "password": "correct-horse-123"},
            headers={"X-Forwarded-For": "198.51.100.20"},
        )
        assert r2.status_code == 201, r2.text

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "auth.register")
            )
        ).scalars().all()
        ips = {row.ip_address for row in rows}
        assert ips == {"198.51.100.10", "198.51.100.20"}
        assert "172.19.0.5" not in ips


async def test_untrusted_forwarded_for_does_not_bypass_login_rate_limit():
    """Baseline sanity: with the default (empty) trusted_proxy_ips, rotating
    a spoofed X-Forwarded-For header per request must not let a caller dodge
    the /auth/login limiter -- reproducing the live repro's "12 requests,
    each with a different X-Forwarded-For" attack at the application layer
    (independent of how uvicorn itself is configured; see
    app/core/rate_limit.py's ClientIPMiddleware docstring for the residual,
    uvicorn-level caveat this does NOT cover)."""
    previous_enabled = limiter.enabled
    limiter.enabled = True
    try:
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            statuses = []
            for i in range(12):
                resp = await client.post(
                    "/auth/login",
                    json={"email": "nobody@example.com", "password": "wrong-password"},
                    headers={"X-Forwarded-For": f"203.0.113.{i}"},
                )
                statuses.append(resp.status_code)
            assert statuses[:10] == [401] * 10
            assert statuses[10:] == [429, 429]
    finally:
        limiter.enabled = previous_enabled


# --------------------------------------------------------------------- #
# E2 -- CORS loopback equivalence
# --------------------------------------------------------------------- #


async def test_loopback_origin_allowed_as_equivalent_in_non_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/auth/login",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"


async def test_untrusted_origin_still_rejected_in_non_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/auth/login",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers


async def test_cors_stays_strict_in_production_no_loopback_widening(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cors_origins", ["http://localhost:3000"])
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.options(
            "/auth/login",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        # Production never widens an explicit origin to its loopback twins.
        assert "access-control-allow-origin" not in resp.headers


# --------------------------------------------------------------------- #
# E3 -- DEBUG/production guard + generic exception handler
# --------------------------------------------------------------------- #


def test_debug_true_with_production_app_env_fails_validation():
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(
            app_env="production",
            debug=True,
            jwt_secret="a" * 40,
            verification_token_secret="a" * 40,
            reset_password_token_secret="a" * 40,
            oauth_state_secret="a" * 40,
            session_secret="a" * 40,
            secret_pepper="a" * 40,
            sso_secret="a" * 40,
            allowed_hosts=["example.com"],
            cors_origins=["https://app.example.com"],
            rate_limit_fail_open=False,
            token_revocation_fail_open=False,
            phone_user_email_domain="phone.example.com",
            wallet_user_email_domain="wallet.example.com",
        )


async def test_unhandled_exception_returns_clean_json_not_traceback(monkeypatch):
    """A genuinely unhandled error from route logic (not an HTTPException)
    must never reach the client as Starlette's debug traceback page --
    reproduced from an unauthenticated endpoint in the original finding.

    Confirmed this fails without the fix by temporarily setting
    ``debug=settings.debug`` (the old app/main.py code, with settings.debug
    monkeypatched to True to mirror the shipped .env): the response then
    switches to ``text/html`` and contains ``RuntimeError`` /
    ``authenticate_email`` frame source in the body instead of the fixed
    JSON envelope.
    """
    app = create_app()

    from app.auth import service as auth_service_module

    async def _boom(self, email, password):
        raise RuntimeError("kaboom-from-authenticate-email")

    monkeypatch.setattr(auth_service_module.AuthService, "authenticate_email", _boom)

    # ServerErrorMiddleware sends the (now clean, JSON) response and then
    # always re-raises the original exception too (so real servers can log
    # it) -- ASGITransport's default raise_app_exceptions=True would
    # propagate that re-raise into this test instead of letting us inspect
    # the response, so it must be disabled here.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/auth/login",
            json={"email": "whoever@example.com", "password": "whatever"},
        )
        assert resp.status_code == 500
        assert resp.headers["content-type"].startswith("application/json")
        body = resp.json()
        assert body == {"detail": {"code": "internal_error", "message": "An unexpected error occurred"}}
        # No filesystem paths / traceback markers leaked to the client.
        assert "Traceback" not in resp.text
        assert "kaboom-from-authenticate-email" not in resp.text


# --------------------------------------------------------------------- #
# E4 -- security headers
# --------------------------------------------------------------------- #


async def test_security_headers_present_on_response():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        resp = await client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "content-security-policy" in resp.headers
        # Plain http test transport -- HSTS must not be sent over a
        # non-HTTPS request.
        assert "strict-transport-security" not in resp.headers
