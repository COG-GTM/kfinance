from typing import Any, Literal, Optional

import click
from fastmcp.tools import FunctionTool
from fastmcp.utilities.logging import get_logger
from langchain_core.utils.function_calling import convert_to_openai_tool

from kfinance.client.kfinance import Client
from kfinance.integrations.local_mcp.kfinance_mcp import KfinanceMcp
from kfinance.integrations.tool_calling.tool_calling_models import KfinanceTool


logger = get_logger(__name__)

# Patterns that a string has to match to be coercible into an integer or a float.
INTEGER_STRING_PATTERN = r"^[+-]?\d+$"
NUMBER_STRING_PATTERN = r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$"


def accept_stringified_numbers(schema: Any) -> Any:
    """Return a copy of a json schema where integer and number types also accept strings.

    Claude returns integer values as strings if they are part of a field that allows
    multiple types, e.g. `start_year: int | None`. Pydantic converts those strings to
    integers, but the low-level mcp sdk validation
    (github.com/modelcontextprotocol/python-sdk/commit/c8bbfc034d5cb876d6b91185cf02da2af6fb8b44)
    is stricter and disallows strings where ints are required. Widening the advertised
    schema keeps that validation enabled while accepting the values models actually send.
    """
    if isinstance(schema, list):
        return [accept_stringified_numbers(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    widened = {key: accept_stringified_numbers(value) for key, value in schema.items()}
    schema_type = widened.get("type")
    if schema_type in ("integer", "number"):
        widened["type"] = [schema_type, "string"]
        # In json schema, `pattern` only applies to strings and is ignored for numbers.
        widened.setdefault(
            "pattern",
            INTEGER_STRING_PATTERN if schema_type == "integer" else NUMBER_STRING_PATTERN,
        )
    return widened


def build_mcp_tool_from_kfinance_tool(kfinance_tool: KfinanceTool) -> FunctionTool:
    """Build an MCP FunctionTool from a langchain KfinanceTool."""

    return FunctionTool(
        name=kfinance_tool.name,
        description=kfinance_tool.description,
        # MCP expects a JSON schema for tool params, which we
        # can generate similar to how langchain generates openai json schemas.
        parameters=accept_stringified_numbers(
            convert_to_openai_tool(kfinance_tool)["function"]["parameters"]
        ),
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
@click.option("--refresh-token", required=False)
@click.option("--client-id", required=False)
@click.option("--private-key", required=False)
def run_mcp(
    transport: Literal["stdio", "sse", "streamable-http"],
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

    :param transport: Transport protocol (stdio, sse, or streamable-http).
    :type transport: Literal["stdio", "sse", "streamable-http"]
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

    logger.info("Server starting")
    kfinance_mcp.run(transport=transport)


if __name__ == "__main__":
    run_mcp()
