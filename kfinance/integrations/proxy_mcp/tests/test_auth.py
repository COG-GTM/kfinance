import logging

import httpx
import pytest

from kfinance.integrations.proxy_mcp.auth import Cache, ClientAccessToken, RefreshTokenDispenser


REFRESH_TOKEN = "s3cr3t-refresh-token"
REFRESH_URL = "https://kfinance.example.com/oauth2/refresh"


def _build_dispenser(handler: httpx.MockTransport) -> RefreshTokenDispenser:
    cache: Cache[ClientAccessToken] = Cache()
    dispenser = RefreshTokenDispenser(
        refresh_token=REFRESH_TOKEN,
        refresh_url=REFRESH_URL,
        cache=cache,
        access_token_cache_key="test",
    )
    dispenser._http_client = httpx.Client(transport=handler)  # noqa: SLF001
    return dispenser


def test_refresh_token_sent_as_query_param() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"access_token": "abc"})

    token = _build_dispenser(httpx.MockTransport(handler)).refresh_access_token()

    assert token.token == "abc"
    assert requests[0].url.params["refresh_token"] == REFRESH_TOKEN


def test_refresh_token_not_in_status_error() -> None:
    dispenser = _build_dispenser(httpx.MockTransport(lambda _: httpx.Response(500)))

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        dispenser.refresh_access_token()

    assert REFRESH_TOKEN not in str(exc_info.value)
    assert REFRESH_TOKEN not in str(exc_info.value.request.url)
    assert REFRESH_TOKEN not in str(exc_info.value.response.request.url)
    assert exc_info.value.response.status_code == 500
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__


def test_refresh_token_not_in_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    dispenser = _build_dispenser(httpx.MockTransport(handler))

    with pytest.raises(httpx.ConnectError) as exc_info:
        dispenser.refresh_access_token()

    assert REFRESH_TOKEN not in str(exc_info.value)
    assert REFRESH_TOKEN not in str(exc_info.value.request.url)


def test_refresh_token_not_in_httpx_log(caplog: pytest.LogCaptureFixture) -> None:
    dispenser = _build_dispenser(
        httpx.MockTransport(lambda _: httpx.Response(200, json={"access_token": "abc"}))
    )

    with caplog.at_level(logging.INFO, logger="httpx"):
        dispenser.refresh_access_token()

    messages = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert any("HTTP Request: GET" in m for m in messages)
    assert all(REFRESH_TOKEN not in m for m in messages)
