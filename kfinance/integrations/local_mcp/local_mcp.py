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


def _read_credential(env_var: str, credential_file: Optional[Path]) -> Optional[str]:
    """Resolve a credential from an environment variable or a file, preferring the file.

    :param env_var: Name of the environment variable holding the credential.
    :type env_var: str
    :param credential_file: Optional path to a file holding the credential.
    :type credential_file: Optional[Path]
    :return: The credential, or None if neither source provides one.
    :rtype: Optional[str]
    """
    if credential_file is not None:
        return credential_file.read_text().strip()
    return os.environ.get(env_var)


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
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar="KFINANCE_REFRESH_TOKEN_FILE",
    required=False,
    help="Path to a file containing the OAuth refresh token.",
)
@click.option(
    "--private-key-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    envvar="KFINANCE_PRIVATE_KEY_FILE",
    required=False,
    help="Path to a file containing the private key for key-pair authentication.",
)
def run_mcp(
    transport: Literal["stdio", "sse", "streamable-http"],
    refresh_token_file: Optional[Path] = None,
    private_key_file: Optional[Path] = None,
) -> None:
    """Run the Kfinance MCP server with specified configuration.

    This function initializes and starts an MCP server that exposes Kfinance
    tools. The server supports multiple authentication methods and
    transport protocols to accommodate different deployment scenarios.

    Credentials are never read from command line arguments because process
    arguments are visible to other users on the host and are persisted in
    shell history. They are read from environment variables
    (KFINANCE_REFRESH_TOKEN, KFINANCE_CLIENT_ID, KFINANCE_PRIVATE_KEY) or
    from files referenced by --refresh-token-file / --private-key-file.

    Authentication Methods (in order of precedence):
    1. Refresh Token: Uses an existing refresh token for authentication
    2. Key Pair: Uses client ID and private key for authentication
    3. Browser: Falls back to browser-based authentication flow

    :param transport: Transport protocol (stdio, sse, or streamable-http).
    :type transport: Literal["stdio", "sse", "streamable-http"]
    :param refresh_token_file: Path to a file holding the OAuth refresh token
    :type refresh_token_file: Optional[Path]
    :param private_key_file: Path to a file holding the private key
    :type private_key_file: Optional[Path]
    """
    logger.info("Server will run with %s transport", transport)
    refresh_token = _read_credential("KFINANCE_REFRESH_TOKEN", refresh_token_file)
    private_key = _read_credential("KFINANCE_PRIVATE_KEY", private_key_file)
    client_id = os.environ.get("KFINANCE_CLIENT_ID")
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
