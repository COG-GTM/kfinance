import os
from typing import Literal, Optional

import click
from fastmcp.tools import FunctionTool
from fastmcp.utilities.logging import get_logger
from langchain_core.utils.function_calling import convert_to_openai_tool
import uvicorn

from kfinance.client.kfinance import Client
from kfinance.integrations.local_mcp.http_security import (
    AUTH_TOKEN_ENV_VAR,
    DEFAULT_HOST,
    LOOPBACK_HOSTNAMES,
    LocalMcpSecurityMiddleware,
    generate_auth_token,
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


def _serve_over_http(
    kfinance_mcp: KfinanceMcp,
    transport: Literal["sse", "streamable-http"],
    host: str,
    port: int,
    auth_token: Optional[str],
) -> None:
    """Serve the MCP server over a network transport behind bearer auth.

    The server acts on behalf of the operator's Kensho credential, so inbound clients
    must authenticate. When no token is configured, a random one is generated and
    logged for the operator to hand to their MCP client.

    :param kfinance_mcp: The MCP server to serve.
    :param transport: The network transport to serve the MCP server over.
    :param host: The address to bind to.
    :param port: The port to bind to.
    :param auth_token: The bearer token inbound clients must present, if preconfigured.
    """
    if auth_token is None:
        auth_token = generate_auth_token()
        logger.warning(
            "No %s configured. Inbound clients must send this generated token: %s",
            AUTH_TOKEN_ENV_VAR,
            auth_token,
        )

    allowed_hosts = LOOPBACK_HOSTNAMES | {host.lower()}
    app = LocalMcpSecurityMiddleware(
        app=kfinance_mcp.http_app(transport=transport),
        auth_token=auth_token,
        allowed_hosts=allowed_hosts,
    )

    logger.info("Server starting on %s:%s", host, port)
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
@click.option("--sse", "transport", flag_value="sse", help="Use SSE transport")
@click.option(
    "--streamable-http",
    "transport",
    flag_value="streamable-http",
    help="Use streamable HTTP transport",
)
@click.option(
    "--host", default=DEFAULT_HOST, help="Host to bind network transports to (default 127.0.0.1)"
)
@click.option("--port", default=8000, type=int, help="Port to bind network transports to")
@click.option("--refresh-token", required=False)
@click.option("--client-id", required=False)
@click.option("--private-key", required=False)
def run_mcp(
    transport: Literal["stdio", "sse", "streamable-http"],
    host: str = DEFAULT_HOST,
    port: int = 8000,
    refresh_token: Optional[str] = None,
    client_id: Optional[str] = None,
    private_key: Optional[str] = None,
) -> None:
    """Run the Kfinance MCP server with specified configuration.

    This function initializes and starts an MCP server that exposes Kfinance
    tools. The server supports multiple authentication methods and
    transport protocols to accommodate different deployment scenarios.

    Upstream authentication methods (in order of precedence):
    1. Refresh Token: Uses an existing refresh token for authentication
    2. Key Pair: Uses client ID and private key for authentication
    3. Browser: Falls back to browser-based authentication flow

    stdio is the default transport. The network transports (sse and streamable-http)
    bind to localhost and require inbound clients to present a bearer token, which is
    read from the KFINANCE_MCP_AUTH_TOKEN environment variable or generated at startup.

    :param transport: Transport protocol (stdio, sse, or streamable-http).
    :type transport: Literal["stdio", "sse", "streamable-http"]
    :param host: Host to bind network transports to.
    :type host: str
    :param port: Port to bind network transports to.
    :type port: int
    :param refresh_token: OAuth refresh token for authentication
    :type refresh_token: str
    :param client_id: Client id for key-pair authentication
    :type client_id: str
    :param private_key: Private key for key-pair authentication.
    :type private_key: str
    """
    logger.info("Server will run with %s transport", transport)
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
        _serve_over_http(
            kfinance_mcp=kfinance_mcp,
            transport=transport,
            host=host,
            port=port,
            auth_token=os.environ.get(AUTH_TOKEN_ENV_VAR) or None,
        )


if __name__ == "__main__":
    run_mcp()
