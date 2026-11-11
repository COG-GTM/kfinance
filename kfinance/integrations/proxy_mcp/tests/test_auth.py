import logging

import httpx
import jwt
import pytest

from kfinance.client.auth_errors import KfinanceAuthenticationError
from kfinance.integrations.proxy_mcp.auth import Cache, ClientAccessToken, RefreshTokenDispenser


REFRESH_TOKEN = "super-secret-refresh-token-value"
REFRESH_URL = "https://kfinance.kensho.com/oauth2/refresh"


def build_dispenser(handler: httpx.MockTransport) -> RefreshTokenDispenser:
    dispenser = RefreshTokenDispenser(
        refresh_token=REFRESH_TOKEN,
        refresh_url=REFRESH_URL,
        cache=Cache[ClientAccessToken](),
        access_token_cache_key="test",
    )
    dispenser._http_client = httpx.Client(transport=handler)  # noqa: SLF001
    return dispenser


class TestRefreshTokenDispenser:
    def test_refresh_success(self, caplog: pytest.LogCaptureFixture) -> None:
        """
        GIVEN a RefreshTokenDispenser
        WHEN the refresh endpoint returns an access token
        THEN the token is returned and the refresh token is not logged by httpx.
        """
        access_token = jwt.encode(
            {"exp": 4102444800}, "test-signing-key-of-at-least-32-bytes", algorithm="HS256"
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"access_token": access_token})

        with caplog.at_level(logging.DEBUG):
            token = build_dispenser(httpx.MockTransport(handler)).access_token

        assert token.token == access_token
        assert requests[0].url.params["refresh_token"] == REFRESH_TOKEN
        assert "HTTP Request" in caplog.text
        assert REFRESH_TOKEN not in caplog.text

    @pytest.mark.parametrize("status_code", [429, 503])
    def test_refresh_failure_does_not_leak_refresh_token(
        self, status_code: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """
        GIVEN a RefreshTokenDispenser
        WHEN the refresh endpoint returns an error
        THEN the raised error and logs do not contain the refresh token.
        """
        dispenser = build_dispenser(httpx.MockTransport(lambda _: httpx.Response(status_code)))

        with caplog.at_level(logging.DEBUG), pytest.raises(KfinanceAuthenticationError) as e:
            _ = dispenser.access_token

        assert e.value.status_code == status_code
        assert REFRESH_TOKEN not in str(e.value)
        assert REFRESH_TOKEN not in caplog.text

    def test_connection_error_does_not_leak_refresh_token(self) -> None:
        """
        GIVEN a RefreshTokenDispenser
        WHEN the refresh request cannot connect
        THEN a KfinanceAuthenticationError without the refresh token is raised.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

        with pytest.raises(KfinanceAuthenticationError, match=r"\(ConnectError\)") as e:
            _ = build_dispenser(httpx.MockTransport(handler)).access_token
        assert REFRESH_TOKEN not in str(e.value)
