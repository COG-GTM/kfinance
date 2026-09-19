from pytest_httpx import HTTPXMock

from kfinance.integrations.proxy_mcp.auth import (
    Cache,
    ClientAccessToken,
    RefreshTokenDispenser,
)


REFRESH_URL = "https://kfinance.kensho.com/oauth2/refresh"


class TestRefreshTokenDispenser:
    def test_refresh_token_sent_in_request_body(self, httpx_mock: HTTPXMock) -> None:
        """The refresh token is posted in the body and never appears in the URL."""
        httpx_mock.add_response(
            method="POST", url=REFRESH_URL, json={"access_token": "fake_access_token"}
        )
        dispenser = RefreshTokenDispenser(
            refresh_token="fake_refresh_token",
            refresh_url=REFRESH_URL,
            cache=Cache[ClientAccessToken](),
            access_token_cache_key="proxy_mcp_token",
        )

        assert dispenser.refresh_access_token().token == "fake_access_token"

        request = httpx_mock.get_requests()[0]
        assert request.content == b"refresh_token=fake_refresh_token"
        assert request.url.query == b""
