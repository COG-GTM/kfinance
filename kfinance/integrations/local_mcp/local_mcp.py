from pathlib import Path
from typing import Literal, Optional

import click
from click.core import ParameterSource
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
PRIVATE_KEY_FILE_ENV_VAR = "KFINANCE_PRIVATE_KEY_FILE"

DEPRECATED_SECRET_FLAG_WARNING = (
    "WARNING: passing {flag} on the command line is deprecated and insecure: command-line "
    "arguments are visible to every user on this host (ps, /proc/<pid>/cmdline) and are kept "
    "in shell history. Set the {env_var} environment variable instead{alternative}."
)


def warn_if_secret_passed_on_command_line(
    ctx: click.Context, param_name: str, flag: str, env_var: str, alternative: str = ""
) -> None:
    """Print a deprecation warning to stderr when a secret came from argv rather than the env."""
    if ctx.get_parameter_source(param_name) == ParameterSource.COMMANDLINE:
        # stdout carries the MCP protocol for the stdio transport, so warn on stderr.
        click.echo(
            DEPRECATED_SECRET_FLAG_WARNING.format(
                flag=flag, env_var=env_var, alternative=alternative
            ),
            err=True,
        )


def resolve_private_key(
    private_key: Optional[str], private_key_file: Optional[Path]
) -> Optional[str]:
    """Return the private key from the inline value or from the key file."""
    if private_key_file is None:
        return private_key
    if private_key:
        raise click.UsageError(
            f"Provide the private key either via {PRIVATE_KEY_ENV_VAR} or via "
            f"--private-key-file/{PRIVATE_KEY_FILE_ENV_VAR}, not both."
        )
    key = private_key_file.read_text().strip()
    if not key:
        raise click.BadParameter(f"{private_key_file} is empty.", param_hint="--private-key-file")
    return key


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
    "--refresh-token",
    envvar=REFRESH_TOKEN_ENV_VAR,
    show_envvar=True,
    required=False,
    help=f"OAuth refresh token. Set {REFRESH_TOKEN_ENV_VAR} instead; the flag is deprecated "
    "because command-line arguments are visible in the process list.",
)
@click.option(
    "--client-id",
    envvar=CLIENT_ID_ENV_VAR,
    show_envvar=True,
    required=False,
    help="Client id for key-pair authentication.",
)
@click.option(
    "--private-key",
    envvar=PRIVATE_KEY_ENV_VAR,
    show_envvar=True,
    required=False,
    help=f"Private key for key-pair authentication. Set {PRIVATE_KEY_ENV_VAR} or use "
    "--private-key-file instead; the flag is deprecated because command-line arguments are "
    "visible in the process list.",
)
@click.option(
    "--private-key-file",
    envvar=PRIVATE_KEY_FILE_ENV_VAR,
    show_envvar=True,
    required=False,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Path to a file containing the private key for key-pair authentication.",
)
@click.pass_context
def run_mcp(
    ctx: click.Context,
    transport: Literal["stdio", "sse", "streamable-http"],
    refresh_token: Optional[str] = None,
    client_id: Optional[str] = None,
    private_key: Optional[str] = None,
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

    Credentials should be supplied through the KFINANCE_REFRESH_TOKEN, KFINANCE_CLIENT_ID and
    KFINANCE_PRIVATE_KEY environment variables (or a private key file). The --refresh-token and
    --private-key flags are deprecated because command-line arguments are visible to every
    local user in the process list and are kept in shell history.

    :param transport: Transport protocol (stdio, sse, or streamable-http).
    :type transport: Literal["stdio", "sse", "streamable-http"]
    :param refresh_token: OAuth refresh token for authentication
    :type refresh_token: str
    :param client_id: Client id for key-pair authentication
    :type client_id: str
    :param private_key: Private key for key-pair authentication.
    :type private_key: str
    :param private_key_file: Path to a file containing the private key.
    :type private_key_file: Path
    """
    warn_if_secret_passed_on_command_line(
        ctx, "refresh_token", "--refresh-token", REFRESH_TOKEN_ENV_VAR
    )
    warn_if_secret_passed_on_command_line(
        ctx,
        "private_key",
        "--private-key",
        PRIVATE_KEY_ENV_VAR,
        alternative=" or pass --private-key-file <path>",
    )
    private_key = resolve_private_key(private_key, private_key_file)

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
