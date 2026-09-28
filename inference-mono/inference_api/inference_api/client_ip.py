"""Trusted-proxy-aware client IP resolution for the serving plane.

Mirrors ``app/core/rate_limit.py``'s ``resolve_client_ip``/``_is_trusted_host``
on the control plane (see that module's docstring for the full trust-model
rationale: honoring ``X-Forwarded-For`` unconditionally lets any client spoof
its own IP, so it is only trusted from a hop this deployment explicitly
configured). This is a deliberately small, standalone copy rather than a
shared import -- the two planes are separate deployables that do not depend on
each other's code -- scoped to the one place this plane currently needs a
first-hand client IP: the anonymous guest-inference abuse limiter (see
``inference_api.guest_limits`` / HYP-67).

Unlike the control plane, this plane has no ``ClientIPMiddleware`` rewriting
``request.client`` up front, so callers must call ``resolve_client_ip(request)``
explicitly rather than reading ``request.client.host`` directly.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

from inference_api.config import settings


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


def resolve_client_ip(request: Request) -> str | None:
    """The trustworthy client IP for ``request``.

    ``X-Forwarded-For`` is honored ONLY when the immediate socket peer matches
    ``settings.trusted_proxy_ips`` (default: empty, i.e. never -- so a bare
    deployment with no reverse proxy configured always uses the raw socket
    peer and cannot be spoofed via this header). When trusted, each proxy is
    assumed to *append* to the header, so we walk the list from the right and
    return the first entry that is not itself a trusted proxy.
    """
    client = request.client
    peer_host = client.host if client else None
    trusted = tuple(settings.trusted_proxy_ips)
    if not trusted:
        return peer_host
    forwarded_for = request.headers.get("x-forwarded-for")
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
