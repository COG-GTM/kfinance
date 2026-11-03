import logging

import httpx
import pytest
from pytest_httpx import HTTPXMock

from kfinance.integrations.proxy_mcp.auth import Cache, ClientAccessToken, RefreshTokenDispenser


REFRESH_URL = "https://kfinance.kensho.com/oauth2/refresh"
REFRESH_TOKEN = "s3cr3t-refresh-token"


def build_dispenser() -> RefreshTokenDispenser:
    return RefreshTokenDispenser(
        refresh_token=REFRESH_TOKEN,
        refresh_url=REFRESH_URL,
        cache=Cache[ClientAccessToken](),
        access_token_cache_key="test",
    )


class TestRefreshTokenDispenser:
    def test_refresh_token_sent_as_query_param(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json={"access_token": "new-access-token"})

        token = build_dispenser().refresh_access_token()

        assert token.token == "new-access-token"
        request = httpx_mock.get_request()
        assert request is not None
        assert request.method == "GET"
        assert request.url.copy_with(query=None) == httpx.URL(REFRESH_URL)
        assert request.url.params["refresh_token"] == REFRESH_TOKEN

    def test_refresh_token_not_written_to_httpx_log(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json={"access_token": "new-access-token"})

        with caplog.at_level(logging.INFO):
            build_dispenser().refresh_access_token()

        request_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
        assert request_lines and "HTTP Request: GET" in request_lines[0]
        assert "refresh_token=[REDACTED]" in request_lines[0]
        assert REFRESH_TOKEN not in caplog.text

    def test_refresh_token_not_in_http_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=401)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            build_dispenser().refresh_access_token()

        assert REFRESH_TOKEN not in str(exc_info.value)
        assert "refresh_token=[REDACTED]" in str(exc_info.value)
        assert exc_info.value.response.status_code == 401
        assert REFRESH_TOKEN not in str(exc_info.value.request.url)
        assert REFRESH_TOKEN not in str(exc_info.value.response.request.url)
