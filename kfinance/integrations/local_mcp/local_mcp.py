import os
from typing import Literal, Optional

import click
from fastmcp.tools import FunctionTool
from fastmcp.utilities.logging import get_logger
from langchain_core.utils.function_calling import convert_to_openai_tool
import uvicorn

from kfinance.client.kfinance import Client
from kfinance.integrations.local_mcp.http_auth import (
    AUTH_TOKEN_ENV_VAR,
    DEFAULT_HOST,
    DEFAULT_PORT,
    LOOPBACK_HOSTS,
    MIN_AUTH_TOKEN_LENGTH,
    BearerAuthMiddleware,
    allowed_hosts_for,
)
from kfinance.integrations.local_mcp.kfinance_mcp import KfinanceMcp
from kfinance.integrations.tool_calling.tool_calling_models import KfinanceTool


logger = get_logger(__name__)


def build_mcp_tool_from_kfinance_tool(kfinance_tool: KfinanceTool) -> FunctionTool:
    """Build an MCP FunctionTool from a langchain KfinanceTool."""

    return FunctionTool(
        name=kfinance_tool.name,
        description=kfinance_tool.description,
        # MCP expects a JSON schema for tool params, which we
        # can generate similar to how langchain generates openai json schemas.
        parameters=convert_to_openai_tool(kfinance_tool)["function"]["parameters"],
        # The langchain runner internally validates input arguments via the args_schema.
        # When running with mcp, we need to reproduce that validation ourselves in
        # arun_without_langchain (which then calls _arun).
        # If we pass in the underlying _arun method directly, mcp generates a schema from
        # the _arun type hints but bypasses our internal validation. This causes errors,
        # for example with integer literals, which our args models allow but the
        # mcp-internal validation disallows.
        # Use the async version to avoid event loop conflicts.
        # fn=kfinance_tool.arun_without_langchain,
        fn=kfinance_tool.arun_without_langchain,
    )


def read_network_auth_token() -> str:
    """Read the bearer token that clients of a network transport must present."""
    auth_token = os.environ.get(AUTH_TOKEN_ENV_VAR, "").strip()
    if len(auth_token) < MIN_AUTH_TOKEN_LENGTH:
        raise click.UsageError(
            f"The --sse and --streamable-http transports require the {AUTH_TOKEN_ENV_VAR} "
            f"environment variable to hold a secret of at least {MIN_AUTH_TOKEN_LENGTH} "
            "characters, which MCP clients must send as 'Authorization: Bearer <token>'. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return auth_token


def serve_over_http(
    kfinance_mcp: KfinanceMcp,
    transport: Literal["sse", "streamable-http"],
    host: str,
    port: int,
    auth_token: str,
) -> None:
    """Serve the MCP server over a network transport behind bearer auth and Host/Origin checks.

    :param kfinance_mcp: The MCP server to serve.
    :param transport: The network transport to use.
    :param host: The address to bind to.
    :param port: The port to bind to.
    :param auth_token: The bearer token clients must present.
    """
    app = BearerAuthMiddleware(
        app=kfinance_mcp.http_app(transport=transport),
        auth_token=auth_token,
        allowed_hosts=allowed_hosts_for(host),
    )
    if host.strip().strip("[]").lower() not in LOOPBACK_HOSTS:
        logger.warning(
            "Binding to non-loopback address %s over plain HTTP: the bearer token is sent "
            "unencrypted. Only expose this port behind a TLS-terminating reverse proxy.",
            host,
        )
    logger.info("Server starting on %s:%s with bearer token authentication", host, port)
    uvicorn.run(app, host=host, port=port)


@click.command()
@click.option(
    "--stdio",
    "-s",
    "transport",
    flag_value="stdio",
    default=True,
    help="Use stdio transport (default)",
)
@click.option(
    "--sse",
    "transport",
    flag_value="sse",
    help=f"Use SSE transport (requires {AUTH_TOKEN_ENV_VAR})",
)
@click.option(
    "--streamable-http",
    "transport",
    flag_value="streamable-http",
    help=f"Use streamable HTTP transport (requires {AUTH_TOKEN_ENV_VAR})",
)
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    help="Address the --sse/--streamable-http transports bind to",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    type=int,
    show_default=True,
    help="Port the --sse/--streamable-http transports bind to",
)
@click.option("--refresh-token", required=False)
@click.option("--client-id", required=False)
@click.option("--private-key", required=False)
def run_mcp(
    transport: Literal["stdio", "sse", "streamable-http"],
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    refresh_token: Optional[str] = None,
    client_id: Optional[str] = None,
    private_key: Optional[str] = None,
) -> None:
    """Run the Kfinance MCP server with specified configuration.

    This function initializes and starts an MCP server that exposes Kfinance
    tools. The server supports multiple authentication methods and
    transport protocols to accommodate different deployment scenarios.

    Authentication Methods (in order of precedence):
    1. Refresh Token: Uses an existing refresh token for authentication
    2. Key Pair: Uses client ID and private key for authentication
    3. Browser: Falls back to browser-based authentication flow

    stdio is the default transport and opens no network port. The sse and streamable-http
    transports require every client to send `Authorization: Bearer <token>`, where the token
    is read from the KFINANCE_MCP_AUTH_TOKEN environment variable, and reject requests whose
    Host or Origin header is not the bound host or localhost.

    :param transport: Transport protocol (stdio, sse, or streamable-http).
    :type transport: Literal["stdio", "sse", "streamable-http"]
    :param host: Address the network transports bind to.
    :type host: str
    :param port: Port the network transports bind to.
    :type port: int
    :param refresh_token: OAuth refresh token for authentication
    :type refresh_token: str
    :param client_id: Client id for key-pair authentication
    :type client_id: str
    :param private_key: Private key for key-pair authentication.
    :type private_key: str
    """
    logger.info("Server will run with %s transport", transport)
    auth_token = None if transport == "stdio" else read_network_auth_token()

    if refresh_token:
        logger.info("The client will be authenticated using a refresh token")
        kfinance_client = Client(refresh_token=refresh_token)
    elif client_id and private_key:
        logger.info("The client will be authenticated using a key pair")
        kfinance_client = Client(client_id=client_id, private_key=private_key)
    else:
        logger.info("The client will be authenticated using a browser")
        kfinance_client = Client()

    kfinance_mcp: KfinanceMcp = KfinanceMcp("Kfinance")
    for langchain_tool in kfinance_client.langchain_tools:
        logger.info("Adding %s to server", langchain_tool.name)
        kfinance_mcp.add_tool(build_mcp_tool_from_kfinance_tool(langchain_tool))

    if transport == "stdio":
        logger.info("Server starting")
        kfinance_mcp.run(transport=transport)
    else:
        assert auth_token is not None
        serve_over_http(
            kfinance_mcp=kfinance_mcp,
            transport=transport,
            host=host,
            port=port,
            auth_token=auth_token,
        )


if __name__ == "__main__":
    run_mcp()
