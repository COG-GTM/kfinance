import os
from pathlib import Path
from typing import Literal, Optional

import click
from fastmcp.tools import FunctionTool
from fastmcp.utilities.logging import get_logger
from langchain_core.utils.function_calling import convert_to_openai_tool

from kfinance.client.kfinance import Client
from kfinance.integrations.local_mcp.kfinance_mcp import KfinanceMcp
from kfinance.integrations.tool_calling.tool_calling_models import KfinanceTool


logger = get_logger(__name__)

REFRESH_TOKEN_ENV_VAR = "KFINANCE_REFRESH_TOKEN"
CLIENT_ID_ENV_VAR = "KFINANCE_CLIENT_ID"
PRIVATE_KEY_ENV_VAR = "KFINANCE_PRIVATE_KEY"

SECRET_FILE_TYPE = click.Path(exists=True, dir_okay=False, readable=True, path_type=Path)


def resolve_secret(secret_file: Optional[Path], env_var: str) -> Optional[str]:
    """Resolve a secret from a file path, falling back to an environment variable.

    Secrets are never accepted as raw CLI values so that they do not leak via
    the process list (argv) or shell history.
    """
    if secret_file is not None:
        secret = secret_file.read_text().strip()
        if not secret:
            raise click.BadParameter(f"{secret_file} is empty.")
        return secret
    return os.environ.get(env_var) or None


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


@click.command()
@click.option("--stdio", "-s", "transport", flag_value="stdio", help="Use stdio transport")
@click.option(
    "--sse", "transport", flag_value="sse", default=True, help="Use SSE transport (default)"
)
@click.option(
    "--streamable-http",
    "transport",
    flag_value="streamable-http",
    help="Use streamable HTTP transport",
)
@click.option(
    "--refresh-token-file",
    type=SECRET_FILE_TYPE,
    help=f"Path to a file containing the refresh token. Defaults to the {REFRESH_TOKEN_ENV_VAR} env var.",
)
@click.option(
    "--client-id",
    envvar=CLIENT_ID_ENV_VAR,
    show_envvar=True,
    help="Client id for key-pair authentication.",
)
@click.option(
    "--private-key-file",
    type=SECRET_FILE_TYPE,
    help=f"Path to a file containing the private key. Defaults to the {PRIVATE_KEY_ENV_VAR} env var.",
)
def run_mcp(
    transport: Literal["stdio", "sse", "streamable-http"],
    refresh_token_file: Optional[Path] = None,
    client_id: Optional[str] = None,
    private_key_file: Optional[Path] = None,
) -> None:
    """Run the Kfinance MCP server with specified configuration.

    This function initializes and starts an MCP server that exposes Kfinance
    tools. The server supports multiple authentication methods and
    transport protocols to accommodate different deployment scenarios.

    Authentication Methods (in order of precedence):
    1. Refresh Token: Uses an existing refresh token for authentication
    2. Key Pair: Uses client ID and private key for authentication
    3. Browser: Falls back to browser-based authentication flow

    Secrets are read from files or environment variables (KFINANCE_REFRESH_TOKEN,
    KFINANCE_PRIVATE_KEY) rather than raw CLI values, so they never appear in the
    process list or shell history.

    :param transport: Transport protocol (stdio, sse, or streamable-http).
    :type transport: Literal["stdio", "sse", "streamable-http"]
    :param refresh_token_file: Path to a file containing an OAuth refresh token
    :type refresh_token_file: Path
    :param client_id: Client id for key-pair authentication
    :type client_id: str
    :param private_key_file: Path to a file containing the private key for key-pair authentication.
    :type private_key_file: Path
    """
    refresh_token = resolve_secret(refresh_token_file, REFRESH_TOKEN_ENV_VAR)
    private_key = resolve_secret(private_key_file, PRIVATE_KEY_ENV_VAR)

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

    logger.info("Server starting")
    kfinance_mcp.run(transport=transport)


if __name__ == "__main__":
    run_mcp()
