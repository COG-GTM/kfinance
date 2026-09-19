from collections.abc import Awaitable, Callable, Collection, Sequence
import ipaddress
import secrets

import click
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import Client
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from fastmcp.utilities.logging import get_logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
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

HEALTH_PATH = "/health"


class ClientTokenAuthMiddleware(BaseHTTPMiddleware):
    """Reject inbound requests that do not present a configured client bearer token.

    The proxy injects the operator credential into every forwarded request, so inbound
    callers must be authenticated to prevent anonymous use of the operator subscription.
    """

    def __init__(self, app: ASGIApp, tokens: Sequence[str], exempt_paths: Collection[str]) -> None:
        """Initialize with the accepted tokens and the paths that skip authentication."""
        super().__init__(app)
        self._tokens = tuple(tokens)
        self._exempt_paths = frozenset(exempt_paths)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Pass the request through only if it carries a valid client token."""
        if request.method == "OPTIONS" or request.url.path in self._exempt_paths:
            return await call_next(request)
        if not self._token_is_valid(request.headers.get("authorization", "")):
            return JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    def _token_is_valid(self, authorization_header: str) -> bool:
        """Return True if the Authorization header holds one of the accepted tokens."""
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return False
        return any(secrets.compare_digest(token, accepted) for accepted in self._tokens)


def _is_loopback(host: str) -> bool:
    """Return True if the host refers to the loopback interface."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
    if not settings.client.tokens and not settings.client.auth_disabled:
        raise ValueError(
            "CLIENT_TOKENS must be set so that inbound clients authenticate to the proxy, "
            "or CLIENT_AUTH_DISABLED must be set to true when an external gateway "
            "authenticates them."
        )

    proxy = build_proxy()
    mcp_http_app = proxy.http_app(path="/mcp", transport="streamable-http")

    app = FastAPI(lifespan=mcp_http_app.lifespan)

    if settings.client.tokens:
        app.add_middleware(
            ClientTokenAuthMiddleware,
            tokens=settings.client.tokens,
            exempt_paths={HEALTH_PATH},
        )
    else:
        logger.warning(
            "Inbound client authentication is disabled: any caller that can reach this "
            "proxy can use the operator credential. Only deploy in this mode behind a "
            "gateway that authenticates callers."
        )

    # Browsers may only call the proxy from explicitly allowed origins. Credentialed
    # requests stay disabled so that a token is always required in the Authorization header.
    if settings.client.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.client.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "mcp-session-id"],
            expose_headers=["mcp-session-id"],
        )

    @app.get(HEALTH_PATH)
    async def health() -> dict:
        return {"status": "healthy"}

    app.mount("/", mcp_http_app)

    return app


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8000, type=int, help="Port to bind to")
def run_proxy_mcp(host: str, port: int) -> None:
    """Run the proxy MCP server."""
    if not _is_loopback(host):
        logger.warning(
            "Binding to non-loopback host %s exposes the proxy on the network. It must "
            "sit behind an authenticating gateway or restricted network.",
            host,
        )

    app = create_app()

    logger.info("Proxy server starting on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_proxy_mcp()
