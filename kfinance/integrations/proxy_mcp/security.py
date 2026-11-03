import secrets
from typing import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


UNAUTHENTICATED_PATHS = frozenset({"/health"})


class InboundAccessMiddleware:
    """Gate every request to the proxy on a pre-shared Bearer token and a trusted Origin.

    The proxy replays each request upstream with the operator's kfinance credentials, so an
    unauthenticated caller would be spending the operator's account. Requests from browsers
    carry an ``Origin`` header; any origin that is not explicitly allowed is rejected so that a
    web page (including one reaching the proxy through DNS rebinding) cannot drive it.
    """

    def __init__(self, app: ASGIApp, token: str, allowed_origins: Iterable[str] = ()) -> None:
        """Initialize with the expected Bearer token and the trusted browser origins."""
        if not token:
            raise ValueError("An inbound token is required")
        self.app = app
        self._token = token.encode()
        self._allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject untrusted origins (403) and missing or wrong Bearer tokens (401)."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and origin not in self._allowed_origins:
            response = JSONResponse({"detail": "Origin not allowed"}, status_code=403)
            await response(scope, receive, send)
            return

        if scope["path"] in UNAUTHENTICATED_PATHS:
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
