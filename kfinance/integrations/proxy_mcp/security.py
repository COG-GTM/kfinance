from ipaddress import ip_address
import secrets
from typing import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


UNAUTHENTICATED_PATHS = frozenset({"/health"})
UNAUTHENTICATED_METHODS = frozenset({"GET", "HEAD"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback(host: str) -> bool:
    """Return whether a bind address or Host name only resolves to the local machine."""
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_name(host_header: str) -> str:
    """Strip the port (and IPv6 brackets) from a Host header value."""
    host_header = host_header.strip().lower()
    if host_header.startswith("["):
        return host_header[1:].partition("]")[0]
    if host_header.count(":") == 1:
        return host_header.partition(":")[0]
    return host_header


class InboundAccessMiddleware:
    """Gate every request to the proxy on a pre-shared Bearer token and a trusted Origin.

    The proxy replays each request upstream with the operator's kfinance credentials, so an
    unauthenticated caller would be spending the operator's account. Requests from browsers
    carry an ``Origin`` header; any origin that is not explicitly allowed is rejected so that a
    web page (including one reaching the proxy through DNS rebinding) cannot drive it. When
    ``allowed_hosts`` is set, requests whose ``Host`` header names any other host are rejected
    as well, which stops DNS-rebinding requests that omit ``Origin``.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        allowed_origins: Iterable[str] = (),
        allowed_hosts: Iterable[str] = (),
    ) -> None:
        """Initialize with the expected Bearer token, trusted browser origins and Host names."""
        if not token:
            raise ValueError("An inbound token is required")
        self.app = app
        self._token = token.encode()
        self._allowed_origins = frozenset(allowed_origins)
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject unknown hosts (421), untrusted origins (403) and bad Bearer tokens (401)."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if self._allowed_hosts and (
            host_header_name(headers.get("host", "")) not in self._allowed_hosts
        ):
            response = JSONResponse({"detail": "Host not allowed"}, status_code=421)
            await response(scope, receive, send)
            return

        origin = headers.get("origin")
        if origin is not None and origin not in self._allowed_origins:
            response = JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            await response(scope, receive, send)
            return

        if scope["path"] in UNAUTHENTICATED_PATHS and scope["method"] in UNAUTHENTICATED_METHODS:
            await self.app(scope, receive, send)
            return

        if not self._is_authorized(headers.get("authorization", "")):
            response = JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _is_authorized(self, header: str) -> bool:
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return secrets.compare_digest(presented.strip().encode(), self._token)
