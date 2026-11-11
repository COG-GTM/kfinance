from unittest.mock import AsyncMock, patch

from fastmcp.client import Client as McpClient
from fastmcp.client.transports import FastMCPTransport
from mcp.types import TextContent
import pytest
from requests_mock import Mocker

from kfinance.client.kfinance import Client
from kfinance.conftest import format_logs_without_requests_mock
from kfinance.domains.companies.company_tools import GetInfoFromIdentifiers
from kfinance.integrations.local_mcp.kfinance_mcp import KfinanceMcp
from kfinance.integrations.local_mcp.local_mcp import build_mcp_tool_from_kfinance_tool


REFRESH_TOKEN = "super-secret-refresh-token-value"


def build_mcp_server(kfinance_client: Client) -> KfinanceMcp:
    kfinance_mcp = KfinanceMcp("Kfinance", mask_error_details=True)
    kfinance_mcp.add_tool(
        build_mcp_tool_from_kfinance_tool(GetInfoFromIdentifiers(kfinance_client=kfinance_client))
    )
    return kfinance_mcp


async def call_get_info(kfinance_mcp: KfinanceMcp) -> str:
    async with McpClient(transport=FastMCPTransport(kfinance_mcp)) as client:
        result = await client.call_tool_mcp("get_info_from_identifiers", {"identifiers": ["SPGI"]})
    assert result.isError
    assert isinstance(result.content[0], TextContent)
    return result.content[0].text


class TestLocalMcpErrors:
    @pytest.mark.asyncio
    async def test_failed_token_refresh_does_not_leak_refresh_token(
        self, requests_mock: Mocker, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        GIVEN a local MCP server whose client is authenticated with a refresh token
        WHEN the token refresh fails with a 503 during a tool call
        THEN the tool error returned to the MCP client and the server logs only contain
            the status code, not the refresh token.
        """
        requests_mock.get("https://kfinance.kensho.com/oauth2/refresh", status_code=503)
        kfinance_mcp = build_mcp_server(Client(refresh_token=REFRESH_TOKEN))

        error_text = await call_get_info(kfinance_mcp)

        assert "HTTP 503" in error_text
        assert REFRESH_TOKEN not in error_text
        assert REFRESH_TOKEN not in format_logs_without_requests_mock(caplog)

    @pytest.mark.asyncio
    async def test_unexpected_error_details_are_masked(self, mock_client: Client) -> None:
        """
        GIVEN a local MCP server
        WHEN a tool raises an unexpected exception
        THEN the exception message is not returned to the MCP client.
        """
        kfinance_mcp = build_mcp_server(mock_client)
        with patch.object(
            GetInfoFromIdentifiers,
            "_arun",
            AsyncMock(side_effect=RuntimeError(f"refresh_token={REFRESH_TOKEN}")),
        ):
            error_text = await call_get_info(kfinance_mcp)

        assert REFRESH_TOKEN not in error_text
