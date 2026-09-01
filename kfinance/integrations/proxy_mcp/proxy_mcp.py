from collections.abc import Awaitable, Callable, Sequence

import click
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastmcp import Client
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from fastmcp.utilities.logging import get_logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp
import uvicorn

from kfinance.integrations.proxy_mcp.auth import (
    Cache,
    ClientAccessToken,
    ClientAccessTokenDispenser,
    DynamicBearerAuth,
    PrivateKeyBasedAccessTokenDispenser,
    RefreshTokenDispenser,
)
from kfinance.integrations.proxy_mcp.settings import settings


logger = get_logger(__name__)

# Headers an MCP client needs to send over streamable-http.
ALLOWED_CORS_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "last-event-id",
    "mcp-protocol-version",
    "mcp-session-id",
]
ALLOWED_CORS_METHODS = ["DELETE", "GET", "OPTIONS", "POST"]


class OriginValidationMiddleware(BaseHTTPMiddleware):
    """Reject requests carrying an ``Origin`` header that is not explicitly allowed.

    The proxy injects the operator's bearer token into every backend request and does
    not authenticate its clients, so a browser page must never be able to drive it.
    CORS response headers alone are not enough: they only stop the attacker page from
    reading the response, not from issuing the request.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        """Store the set of origins that are allowed to reach the proxy."""
        super().__init__(app)
        self.allowed_origins = set(allowed_origins)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Return 403 for cross-origin requests from origins that are not allowlisted."""
        origin = request.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            logger.warning("Rejected request from disallowed origin %s", origin)
            return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})
        return await call_next(request)


def add_security_middleware(app: FastAPI) -> None:
    """Restrict which hosts and browser origins can reach the proxy.

    Middleware runs in reverse registration order, so CORS handling for allowed origins
    happens first, then the origin allowlist, then the host allowlist.
    """
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(OriginValidationMiddleware, allowed_origins=settings.allowed_origins)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=False,
            allow_methods=ALLOWED_CORS_METHODS,
            allow_headers=ALLOWED_CORS_HEADERS,
            expose_headers=["mcp-session-id"],
        )


def _build_dispenser() -> ClientAccessTokenDispenser:
    """Build the appropriate token dispenser based on settings."""
    cache: Cache[ClientAccessToken] = Cache()

    if settings.auth.client_id and settings.auth.private_key:
        return PrivateKeyBasedAccessTokenDispenser(
            client_id=settings.auth.client_id,
            private_key=settings.auth.private_key,
            cache=cache,
            access_token_cache_key="proxy_mcp_token",
            okta_host=settings.auth.okta_host,
        )
    elif settings.auth.refresh_token:
        return RefreshTokenDispenser(
            refresh_token=settings.auth.refresh_token,
            refresh_url=settings.auth.refresh_url,
            cache=cache,
            access_token_cache_key="proxy_mcp_token",
        )
    else:
        raise ValueError(
            "Either AUTH_CLIENT_ID and AUTH_PRIVATE_KEY, or AUTH_REFRESH_TOKEN must be set"
        )


def build_proxy() -> FastMCPProxy:
    """Build a FastMCPProxy that injects a Bearer token into every request to the backend."""
    logger.info("Proxy will forward to %s", settings.backend_url)

    dispenser = _build_dispenser()
    auth = DynamicBearerAuth(dispenser)

    base_client: ProxyClient = ProxyClient(settings.backend_url, auth=auth)

    def client_factory() -> Client:
        return base_client.new()

    return FastMCPProxy(client_factory=client_factory, name="Kfinance Proxy")


def create_app() -> FastAPI:
    """Create the FastAPI application wrapping the MCP proxy."""
    proxy = build_proxy()
    mcp_http_app = proxy.http_app(path="/mcp", transport="streamable-http")

    app = FastAPI(lifespan=mcp_http_app.lifespan)
    add_security_middleware(app)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    app.mount("/", mcp_http_app)

    return app


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to bind to")
def run_proxy_mcp(host: str, port: int) -> None:
    """Run the proxy MCP server."""
    app = create_app()

    logger.info("Proxy server starting on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_proxy_mcp()
