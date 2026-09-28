from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest

from kfinance.integrations.local_mcp.local_mcp import run_mcp


@pytest.fixture
def mock_client():
    with (
        patch("kfinance.integrations.local_mcp.local_mcp.Client") as client,
        patch("kfinance.integrations.local_mcp.local_mcp.KfinanceMcp") as mcp,
    ):
        client.return_value.langchain_tools = []
        mcp.return_value = MagicMock()
        yield client


class TestRunMcpCredentials:
    def test_refresh_token_from_env(self, mock_client: MagicMock) -> None:
        """
        GIVEN KFINANCE_REFRESH_TOKEN is set
        WHEN the local mcp server starts
        THEN the client is built with that refresh token
        """
        result = CliRunner().invoke(run_mcp, [], env={"KFINANCE_REFRESH_TOKEN": "token"})
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(refresh_token="token")

    def test_refresh_token_from_file(self, mock_client: MagicMock, tmp_path: Path) -> None:
        """
        GIVEN a refresh token file and a different KFINANCE_REFRESH_TOKEN
        WHEN the local mcp server starts with --refresh-token-file
        THEN the token from the file takes precedence
        """
        token_file = tmp_path / "token"
        token_file.write_text("file-token\n")
        result = CliRunner().invoke(
            run_mcp,
            ["--refresh-token-file", str(token_file)],
            env={"KFINANCE_REFRESH_TOKEN": "env-token"},
        )
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(refresh_token="file-token")

    def test_key_pair_from_env(self, mock_client: MagicMock) -> None:
        """
        GIVEN KFINANCE_CLIENT_ID and KFINANCE_PRIVATE_KEY are set
        WHEN the local mcp server starts
        THEN the client is built with the key pair
        """
        result = CliRunner().invoke(
            run_mcp,
            [],
            env={
                "KFINANCE_REFRESH_TOKEN": None,
                "KFINANCE_CLIENT_ID": "client",
                "KFINANCE_PRIVATE_KEY": "key",
            },
        )
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(client_id="client", private_key="key")

    def test_private_key_from_file(self, mock_client: MagicMock, tmp_path: Path) -> None:
        """
        GIVEN a private key file
        WHEN the local mcp server starts with --client-id and --private-key-file
        THEN the client is built with the key pair from the file
        """
        key_file = tmp_path / "key.pem"
        key_file.write_text("pem-key\n")
        result = CliRunner().invoke(
            run_mcp,
            ["--client-id", "client", "--private-key-file", str(key_file)],
            env={"KFINANCE_REFRESH_TOKEN": None, "KFINANCE_PRIVATE_KEY": None},
        )
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(client_id="client", private_key="pem-key")

    def test_browser_fallback(self, mock_client: MagicMock) -> None:
        """
        GIVEN no credentials are configured
        WHEN the local mcp server starts
        THEN the client falls back to browser auth
        """
        result = CliRunner().invoke(
            run_mcp,
            [],
            env={
                "KFINANCE_REFRESH_TOKEN": None,
                "KFINANCE_CLIENT_ID": None,
                "KFINANCE_PRIVATE_KEY": None,
            },
        )
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with()

    @pytest.mark.parametrize("flag", ["--refresh-token", "--private-key"])
    def test_raw_secret_flags_rejected(self, mock_client: MagicMock, flag: str) -> None:
        """
        GIVEN a secret passed as a raw CLI value
        WHEN the local mcp server starts
        THEN the command fails without building a client
        """
        result = CliRunner().invoke(run_mcp, [flag, "secret"])
        assert result.exit_code != 0
        mock_client.assert_not_called()
