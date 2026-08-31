from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

limiter = Limiter(
    # By the time this key_func runs, ClientIPMiddleware (added in
    # app/main.py) has already resolved and written the trustworthy client IP
    # into ``scope["client"]``, so plain ``get_remote_address`` (which just
    # reads ``request.client.host``) is safe -- see ClientIPMiddleware below
    # for why that ordering matters.
    key_func=get_remote_address,
    storage_uri=settings.rate_limit_url,
    default_limits=[],
    headers_enabled=False,
    swallow_errors=settings.rate_limit_fail_open,
    enabled=settings.rate_limit_enabled,
)


def _is_trusted_host(host: str | None, trusted: tuple[str, ...]) -> bool:
    """Is ``host`` one of the configured trusted reverse-proxy hops?

    Accepts both literal IPs and CIDR networks in ``trusted``. Empty/None
    input is never trusted (fail closed).
    """
    if not host or not trusted:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host in trusted
    for entry in trusted:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            elif addr == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def resolve_client_ip(peer_host: str | None, forwarded_for: str | None) -> str | None:
    """Derive the trustworthy client IP for a request.

    ``peer_host`` is whatever the ASGI server currently reports as the
    connecting peer. IMPORTANT: when uvicorn is run with ``proxy_headers=True``
    (the default) it runs its OWN ``ProxyHeadersMiddleware`` outside of this
    application, and if that middleware's ``forwarded_allow_ips`` /
    ``FORWARDED_ALLOW_IPS`` considers the immediate socket peer trusted, it has
    ALREADY overwritten the scope's ``client`` with the first untrusted entry
    of ``X-Forwarded-For`` before this code ever runs -- there is no way for
    application code to recover the original socket peer in that case. See
    the module docstring on ``ClientIPMiddleware`` and the operator guidance in
    the E1 report for how to configure the two layers together.

    Given that caveat, this function applies its OWN, independent trust
    decision on top of whatever ``peer_host`` it is handed: ``X-Forwarded-For``
    is honored ONLY when ``peer_host`` matches ``settings.trusted_proxy_ips``
    (default: empty, i.e. never). When trusted, each proxy is assumed to
    *append* to the header (standard behavior), so we walk the list from the
    right and return the first entry that is not itself a trusted proxy --
    mirroring uvicorn's own ``_TrustedHosts.get_trusted_client_address``
    algorithm so the two layers agree when both are configured consistently.
    """
    trusted = tuple(settings.trusted_proxy_ips)
    if forwarded_for and _is_trusted_host(peer_host, trusted):
        chain = [item.strip() for item in forwarded_for.split(",") if item.strip()]
        for host in reversed(chain):
            if not _is_trusted_host(host, trusted):
                return host
        if chain:
            # Every hop declared was itself "trusted" -- fall back to the
            # left-most (originating) entry rather than trusting nothing.
            return chain[0]
    return peer_host


class ClientIPMiddleware:
    """Pure-ASGI middleware that pins ``scope["client"]`` to the trustworthy IP.

    This must be the OUTERMOST middleware registered on the app (added last
    via ``app.add_middleware``, see app/main.py) so it runs before the SlowAPI
    limiter, CORS/TrustedHost checks, and every route handler -- including
    ``app/audit/service.py``'s own ``ip_address`` capture and
    ``app/auth/service.py`` / ``app/auth/phone.py``'s ``request.client.host``
    reads. Because it rewrites ``request.client`` in place, all of those call
    sites automatically converge on the same value without needing their own
    changes.

    It also strips any ``x-forwarded-for`` header from the scope once
    consumed, so that code which naively re-parses that header directly
    (rather than reading ``request.client``) cannot be handed a value that
    disagrees with what the limiter used.
    """

    def __init__(self, app: "ASGIApp") -> None:
        self.app = app

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer_host, peer_port = (client[0], client[1]) if client else (None, 0)

        forwarded_for: str | None = None
        remaining_headers = []
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                if forwarded_for is None:
                    forwarded_for = value.decode("latin1")
                else:
                    forwarded_for = f"{forwarded_for}, {value.decode('latin1')}"
                continue
            remaining_headers.append((name, value))

        resolved = resolve_client_ip(peer_host, forwarded_for)
        scope["client"] = (resolved, peer_port) if resolved else client
        scope["headers"] = remaining_headers

        await self.app(scope, receive, send)
