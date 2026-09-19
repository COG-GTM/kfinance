from ipaddress import ip_address
import secrets

import click
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastmcp import Client
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from fastmcp.utilities.logging import get_logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
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

UNAUTHENTICATED_PATHS = frozenset({"/health"})


class InboundBearerTokenMiddleware(BaseHTTPMiddleware):
    """Require a pre-shared Bearer token on every request that reaches the proxy.

    The proxy injects the operator's backend credential into forwarded requests, so callers
    must be authenticated here to prevent anonymous use of the operator's subscription.
    """

    def __init__(self, app: object, token: str) -> None:
        """Initialize with the expected Bearer token."""
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Reject requests that do not carry the expected Bearer token."""
        if request.url.path in UNAUTHENTICATED_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(presented, self._token):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)


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
    if not settings.inbound.token:
        raise ValueError(
            "INBOUND_TOKEN must be set so that callers of the proxy are authenticated. "
            "Without it, anyone who can reach the proxy could use the operator's backend "
            "credentials."
        )

    proxy = build_proxy()
    mcp_http_app = proxy.http_app(path="/mcp", transport="streamable-http")

    app = FastAPI(lifespan=mcp_http_app.lifespan)
    # Added first so that CORSMiddleware, added last, wraps it and annotates 401 responses.
    app.add_middleware(InboundBearerTokenMiddleware, token=settings.inbound.token)
    if settings.inbound.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.inbound.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Last-Event-ID",
                "Mcp-Session-Id",
                "Mcp-Protocol-Version",
            ],
            expose_headers=["Mcp-Session-Id"],
        )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    app.mount("/", mcp_http_app)

    return app


def _is_loopback(host: str) -> bool:
    """Return True if the bind host only accepts loopback connections."""
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to bind to")
def run_proxy_mcp(host: str, port: int) -> None:
    """Run the proxy MCP server."""
    if not _is_loopback(host):
        logger.warning(
            "Binding to non-loopback host %s exposes the proxy to the network. Deploy it behind "
            "an authenticating gateway.",
            host,
        )

    app = create_app()

    logger.info("Proxy server starting on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_proxy_mcp()
