from collections.abc import Iterator
import json

import pytest
import requests

from kfinance.client.server_thread import ServerThread


EXPECTED_ORIGIN = "https://kfinance.kensho.com"


@pytest.fixture
def server_thread() -> Iterator[ServerThread]:
    thread = ServerThread(expected_origin=EXPECTED_ORIGIN)
    thread.start()
    yield thread
    if thread.is_alive():
        # The server handles one request at a time, so it needs a final request to leave its loop.
        requests.post(
            f"http://127.0.0.1:{thread.server_port}",
            headers={"Origin": EXPECTED_ORIGIN},
            data=json.dumps({"state": thread.state, "refresh_token": "shutdown_token"}),
            timeout=5,
        )
        thread.join(timeout=5)
    thread.server.server_close()


class TestServerThread:
    def test_post_with_valid_state_sets_refresh_token(self, server_thread: ServerThread) -> None:
        """A post from the login page with the login state sets the refresh token."""
        response = requests.post(
            f"http://127.0.0.1:{server_thread.server_port}",
            headers={"Origin": EXPECTED_ORIGIN},
            data=json.dumps({"state": server_thread.state, "refresh_token": "valid_token"}),
            timeout=5,
        )
        server_thread.join(timeout=5)

        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == EXPECTED_ORIGIN
        assert "Access-Control-Allow-Credentials" not in response.headers
        assert server_thread.refresh_token == "valid_token"

    @pytest.mark.parametrize(
        "headers, body",
        [
            pytest.param(
                {"Origin": EXPECTED_ORIGIN},
                {"refresh_token": "attacker_token"},
                id="missing state",
            ),
            pytest.param(
                {"Origin": EXPECTED_ORIGIN},
                {"state": "wrong_state", "refresh_token": "attacker_token"},
                id="wrong state",
            ),
            pytest.param(
                {"Origin": "https://evil.example.com"},
                {"refresh_token": "attacker_token"},
                id="cross origin",
            ),
            pytest.param(
                {"Referer": "https://evil.example.com/page"},
                {"refresh_token": "attacker_token"},
                id="cross site referer",
            ),
        ],
    )
    def test_post_without_valid_state_or_origin_is_rejected(
        self, server_thread: ServerThread, headers: dict[str, str], body: dict[str, str]
    ) -> None:
        """Posts that cannot be tied to this login attempt do not set the refresh token."""
        response = requests.post(
            f"http://127.0.0.1:{server_thread.server_port}",
            headers=headers,
            data=json.dumps(body),
            timeout=5,
        )

        assert response.status_code in (400, 403)
        assert "Access-Control-Allow-Origin" not in response.headers
        assert server_thread.refresh_token is None

    def test_oversized_body_is_rejected(self, server_thread: ServerThread) -> None:
        """A body larger than the cap is rejected without being read into a token."""
        response = requests.post(
            f"http://127.0.0.1:{server_thread.server_port}",
            headers={"Origin": EXPECTED_ORIGIN},
            data=json.dumps({"state": server_thread.state, "refresh_token": "a" * 10000}),
            timeout=5,
        )

        assert response.status_code == 413
        assert server_thread.refresh_token is None

    @pytest.mark.parametrize("method", ["get", "head"])
    def test_files_are_not_served(self, server_thread: ServerThread, method: str) -> None:
        """The handler never serves files from the working directory."""
        response = getattr(requests, method)(
            f"http://127.0.0.1:{server_thread.server_port}/pyproject.toml", timeout=5
        )

        assert response.status_code == 405
        assert not response.content
