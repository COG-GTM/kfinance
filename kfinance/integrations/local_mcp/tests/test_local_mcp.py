from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest
from starlette.testclient import TestClient

from kfinance.integrations.local_mcp import local_mcp
from kfinance.integrations.local_mcp.http_auth import (
    AUTH_TOKEN_ENV_VAR,
    BearerAuthMiddleware,
    allowed_hosts_for,
)
from kfinance.integrations.local_mcp.kfinance_mcp import KfinanceMcp


TOKEN = "k" * 43


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.langchain_tools = []
    with patch.object(local_mcp, "Client", return_value=client) as client_cls:
        yield client_cls


class TestRunMcpTransport:
    def test_stdio_is_the_default_and_opens_no_port(self, mock_client, monkeypatch) -> None:
        """Running without transport flags uses stdio and never starts an HTTP server."""
        monkeypatch.delenv(AUTH_TOKEN_ENV_VAR, raising=False)
        with (
            patch.object(KfinanceMcp, "run") as run,
            patch.object(local_mcp, "serve_over_http") as serve,
            patch.object(local_mcp.uvicorn, "run") as uvicorn_run,
        ):
            result = CliRunner().invoke(local_mcp.run_mcp, ["--refresh-token", "rt"])
        assert result.exit_code == 0, result.output
        run.assert_called_once_with(transport="stdio")
        serve.assert_not_called()
        uvicorn_run.assert_not_called()

    @pytest.mark.parametrize("flag", ["--sse", "--streamable-http"])
    @pytest.mark.parametrize("token", [None, "", "too-short"])
    def test_network_transport_requires_auth_token(
        self, mock_client, monkeypatch, flag: str, token: str | None
    ) -> None:
        """Network transports refuse to start without a strong client auth token."""
        if token is None:
            monkeypatch.delenv(AUTH_TOKEN_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, token)
        with patch.object(local_mcp.uvicorn, "run") as uvicorn_run:
            result = CliRunner().invoke(local_mcp.run_mcp, [flag, "--refresh-token", "rt"])
        assert result.exit_code == 2
        assert AUTH_TOKEN_ENV_VAR in result.output
        uvicorn_run.assert_not_called()
        mock_client.assert_not_called()

    @pytest.mark.parametrize(
        ("flag", "transport"), [("--sse", "sse"), ("--streamable-http", "streamable-http")]
    )
    def test_network_transport_is_served_behind_auth(
        self, mock_client, monkeypatch, flag: str, transport: str
    ) -> None:
        """Network transports are wrapped in BearerAuthMiddleware and bound to loopback."""
        monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, TOKEN)
        with (
            patch.object(KfinanceMcp, "run") as run,
            patch.object(local_mcp.uvicorn, "run") as uvicorn_run,
        ):
            result = CliRunner().invoke(local_mcp.run_mcp, [flag, "--refresh-token", "rt"])
        assert result.exit_code == 0, result.output
        run.assert_not_called()
        uvicorn_run.assert_called_once()
        app = uvicorn_run.call_args.args[0]
        assert isinstance(app, BearerAuthMiddleware)
        assert uvicorn_run.call_args.kwargs == {"host": "127.0.0.1", "port": 8000}


class TestHttpAppAuth:
    """End-to-end checks against the real FastMCP streamable HTTP app."""

    INITIALIZE = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    HEADERS = {"Accept": "application/json, text/event-stream"}

    def build_client(self) -> TestClient:
        app = BearerAuthMiddleware(
            KfinanceMcp("Kfinance").http_app(transport="streamable-http"),
            auth_token=TOKEN,
            allowed_hosts=allowed_hosts_for("127.0.0.1"),
        )
        return TestClient(app, base_url="http://127.0.0.1:8000")

    def test_unauthenticated_request_is_rejected(self) -> None:
        """An MCP initialize request without the bearer token is rejected."""
        with self.build_client() as client:
            resp = client.post("/mcp", json=self.INITIALIZE, headers=self.HEADERS)
        assert resp.status_code == 401

    def test_authenticated_request_is_served(self) -> None:
        """An MCP initialize request with the bearer token is served."""
        with self.build_client() as client:
            resp = client.post(
                "/mcp",
                json=self.INITIALIZE,
                headers={**self.HEADERS, "Authorization": f"Bearer {TOKEN}"},
            )
        assert resp.status_code == 200
        assert "serverInfo" in resp.text
