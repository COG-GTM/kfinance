from collections.abc import Awaitable, Callable
import secrets

import click
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import Client
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient
from fastmcp.utilities.logging import get_logger
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
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


def _validated_allowed_origins() -> list[str]:
    """Return configured origins, rejecting wildcard CORS."""
    allowed_origins = settings.client.allowed_origins
    if "*" in allowed_origins:
        raise ValueError(
            "Wildcard CORS origins are not allowed because the proxy forwards a privileged "
            "service token."
        )
    return allowed_origins


def create_app() -> FastAPI:
    """Create the FastAPI application wrapping the MCP proxy."""
    if settings.client.api_key is None:
        raise ValueError(
            "CLIENT_API_KEY must be set so the proxy authenticates incoming clients; "
            "it forwards a privileged service token to the kfinance backend."
        )

    api_key = settings.client.api_key
    allowed_origins = _validated_allowed_origins()
    proxy = build_proxy()
    mcp_http_app = proxy.http_app(path="/mcp", transport="streamable-http")

    app = FastAPI(lifespan=mcp_http_app.lifespan)

    @app.middleware("http")
    async def authenticate_client(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path != "/health":
            expected = f"Bearer {api_key}"
            provided = request.headers.get("authorization", "")
            if not secrets.compare_digest(provided, expected):
                return JSONResponse(
                    {"detail": "Unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Mcp-Session-Id",
                "Mcp-Protocol-Version",
                "Last-Event-ID",
            ],
            expose_headers=["Mcp-Session-Id"],
        )

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
