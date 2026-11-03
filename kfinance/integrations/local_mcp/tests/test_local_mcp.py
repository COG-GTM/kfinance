from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest

from kfinance.integrations.local_mcp import local_mcp
from kfinance.integrations.local_mcp.local_mcp import run_mcp


CREDENTIAL_ENV_VARS = (
    local_mcp.REFRESH_TOKEN_ENV_VAR,
    local_mcp.CLIENT_ID_ENV_VAR,
    local_mcp.PRIVATE_KEY_ENV_VAR,
    local_mcp.PRIVATE_KEY_FILE_ENV_VAR,
)


@pytest.fixture
def mock_client() -> Iterator[MagicMock]:
    with (
        patch.object(local_mcp, "Client") as client,
        patch.object(local_mcp, "KfinanceMcp") as kfinance_mcp,
    ):
        client.return_value.langchain_tools = []
        yield client
        kfinance_mcp.return_value.run.assert_called_once()


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    for env_var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)
    return CliRunner()


class TestRunMcpCredentials:
    def test_refresh_token_from_env(self, runner: CliRunner, mock_client: MagicMock) -> None:
        """
        GIVEN KFINANCE_REFRESH_TOKEN is set
        WHEN run_mcp starts without credential flags
        THEN the client is built with that refresh token and no deprecation warning is shown
        """
        result = runner.invoke(
            run_mcp, ["--stdio"], env={local_mcp.REFRESH_TOKEN_ENV_VAR: "env-token"}
        )
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(refresh_token="env-token")
        assert "deprecated" not in result.stderr

    def test_key_pair_from_env(self, runner: CliRunner, mock_client: MagicMock) -> None:
        """
        GIVEN KFINANCE_CLIENT_ID and KFINANCE_PRIVATE_KEY are set
        WHEN run_mcp starts
        THEN the client is built with the key pair
        """
        result = runner.invoke(
            run_mcp,
            ["--stdio"],
            env={local_mcp.CLIENT_ID_ENV_VAR: "cid", local_mcp.PRIVATE_KEY_ENV_VAR: "pk"},
        )
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(client_id="cid", private_key="pk")
        assert "deprecated" not in result.stderr

    @pytest.mark.parametrize("use_env_var", [False, True])
    def test_private_key_from_file(
        self, runner: CliRunner, mock_client: MagicMock, tmp_path: Path, use_env_var: bool
    ) -> None:
        """
        GIVEN a private key stored in a file
        WHEN run_mcp gets the path via --private-key-file or KFINANCE_PRIVATE_KEY_FILE
        THEN the client is built with the file contents
        """
        key_file = tmp_path / "key.pem"
        key_file.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
        env = {local_mcp.CLIENT_ID_ENV_VAR: "cid"}
        args = ["--stdio"]
        if use_env_var:
            env[local_mcp.PRIVATE_KEY_FILE_ENV_VAR] = str(key_file)
        else:
            args += ["--private-key-file", str(key_file)]

        result = runner.invoke(run_mcp, args, env=env)

        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(
            client_id="cid",
            private_key="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        )
        assert "deprecated" not in result.stderr

    def test_no_credentials_uses_browser(self, runner: CliRunner, mock_client: MagicMock) -> None:
        """
        GIVEN no credentials
        WHEN run_mcp starts
        THEN the client falls back to browser login
        """
        result = runner.invoke(run_mcp, ["--stdio"])
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with()

    @pytest.mark.parametrize(
        "args, env, expected_client_kwargs, flag",
        [
            (
                ["--refresh-token", "cli-token"],
                {},
                {"refresh_token": "cli-token"},
                "--refresh-token",
            ),
            (
                ["--client-id", "cid", "--private-key", "cli-pk"],
                {},
                {"client_id": "cid", "private_key": "cli-pk"},
                "--private-key",
            ),
        ],
    )
    def test_deprecated_cli_flags_still_work_with_warning(
        self,
        runner: CliRunner,
        mock_client: MagicMock,
        args: list[str],
        env: dict[str, str],
        expected_client_kwargs: dict[str, str],
        flag: str,
    ) -> None:
        """
        GIVEN a secret passed as a command-line flag
        WHEN run_mcp starts
        THEN it still authenticates but warns on stderr (not stdout, which carries stdio MCP)
        """
        result = runner.invoke(run_mcp, ["--stdio", *args], env=env)
        assert result.exit_code == 0, result.output
        mock_client.assert_called_once_with(**expected_client_kwargs)
        assert f"passing {flag} on the command line is deprecated" in result.stderr
        assert "deprecated" not in result.stdout


class TestRunMcpPrivateKeyValidation:
    def test_private_key_and_file_conflict(self, runner: CliRunner, tmp_path: Path) -> None:
        """
        GIVEN both KFINANCE_PRIVATE_KEY and a private key file
        WHEN run_mcp starts
        THEN it exits with a usage error instead of picking one silently
        """
        key_file = tmp_path / "key.pem"
        key_file.write_text("file-key")
        with patch.object(local_mcp, "Client") as client:
            result = runner.invoke(
                run_mcp,
                ["--stdio", "--private-key-file", str(key_file)],
                env={local_mcp.CLIENT_ID_ENV_VAR: "cid", local_mcp.PRIVATE_KEY_ENV_VAR: "pk"},
            )
        assert result.exit_code == 2
        assert "not both" in result.stderr
        client.assert_not_called()

    def test_empty_private_key_file(self, runner: CliRunner, tmp_path: Path) -> None:
        """
        GIVEN an empty private key file
        WHEN run_mcp starts
        THEN it exits with a parameter error
        """
        key_file = tmp_path / "key.pem"
        key_file.write_text("\n")
        with patch.object(local_mcp, "Client") as client:
            result = runner.invoke(
                run_mcp,
                ["--stdio", "--private-key-file", str(key_file)],
                env={local_mcp.CLIENT_ID_ENV_VAR: "cid"},
            )
        assert result.exit_code == 2
        assert "is empty" in result.stderr
        client.assert_not_called()
