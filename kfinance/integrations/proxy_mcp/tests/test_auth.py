from pytest_httpx import HTTPXMock

from kfinance.integrations.proxy_mcp.auth import Cache, ClientAccessToken, RefreshTokenDispenser


REFRESH_URL = "https://kfinance.kensho.com/oauth2/refresh"


class TestRefreshTokenDispenser:
    def test_refresh_token_sent_in_post_body_not_url(self, httpx_mock: HTTPXMock) -> None:
        """
        WHEN the proxy exchanges its refresh token for an access token
        THEN the token is sent in a form-encoded POST body and never in the URL
        """
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

        [request] = httpx_mock.get_requests()
        assert request.method == "POST"
        assert request.url.query == b""
        assert "fake_refresh_token" not in str(request.url)
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
        assert request.content == b"refresh_token=fake_refresh_token"
