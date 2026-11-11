import logging

import pytest
import requests
from requests_mock import Mocker

from kfinance.client.fetch import DEFAULT_API_HOST, KFinanceApiClient
from kfinance.client.refresh_token_redaction import (
    REDACTED,
    RefreshTokenRedactingFilter,
    install_refresh_token_log_filter,
    redact_refresh_token,
)


REFRESH_TOKEN = "s3cr3t-refresh-token"


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            f"GET https://h/oauth2/refresh?refresh_token={REFRESH_TOKEN} 200",
            f"GET https://h/oauth2/refresh?refresh_token={REDACTED} 200",
        ),
        (
            f"https://h/r?a=1&refresh_token={REFRESH_TOKEN}&b=2",
            f"https://h/r?a=1&refresh_token={REDACTED}&b=2",
        ),
        (
            f"https://h/r?refresh%5Ftoken={REFRESH_TOKEN}",
            f"https://h/r?refresh%5Ftoken={REDACTED}",
        ),
        ("no secrets here", "no secrets here"),
    ],
)
def test_redact_refresh_token(text: str, expected: str) -> None:
    assert redact_refresh_token(text) == expected


def test_filter_redacts_urllib3_request_line() -> None:
    record = logging.LogRecord(
        name="urllib3.connectionpool",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg='%s://%s:%s "%s %s %s" %s %s',
        args=(
            "https",
            "kfinance.kensho.com",
            443,
            "GET",
            f"/oauth2/refresh?refresh_token={REFRESH_TOKEN}",
            "HTTP/1.1",
            200,
            None,
        ),
        exc_info=None,
    )

    assert RefreshTokenRedactingFilter().filter(record)
    assert REFRESH_TOKEN not in record.getMessage()


def test_install_is_idempotent() -> None:
    install_refresh_token_log_filter()
    install_refresh_token_log_filter()
    for name in ("httpx", "urllib3.connectionpool"):
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, RefreshTokenRedactingFilter) for f in filters) == 1


class TestRefreshTokenExchange:
    def test_refresh_token_sent_as_query_param(self, requests_mock: Mocker) -> None:
        requests_mock.get(f"{DEFAULT_API_HOST}/oauth2/refresh", json={"access_token": "abc"})
        client = KFinanceApiClient(refresh_token=REFRESH_TOKEN)

        assert client._get_access_token_via_refresh_token() == "abc"  # noqa: SLF001
        assert requests_mock.last_request.qs == {"refresh_token": [REFRESH_TOKEN]}

    def test_refresh_token_not_in_http_error(self, requests_mock: Mocker) -> None:
        requests_mock.get(f"{DEFAULT_API_HOST}/oauth2/refresh", status_code=500)
        client = KFinanceApiClient(refresh_token=REFRESH_TOKEN)

        with pytest.raises(requests.HTTPError) as exc_info:
            client._get_access_token_via_refresh_token()  # noqa: SLF001

        assert str(exc_info.value) == "Refresh token exchange failed with HTTP status 500"
        assert exc_info.value.request is None
        assert exc_info.value.response is not None
        assert exc_info.value.response.status_code == 500
        assert exc_info.value.response.url is None
        assert exc_info.value.__suppress_context__

    def test_refresh_token_not_in_connection_error(self, requests_mock: Mocker) -> None:
        requests_mock.get(
            f"{DEFAULT_API_HOST}/oauth2/refresh",
            exc=requests.ConnectionError(
                f"Max retries exceeded with url: /oauth2/refresh?refresh_token={REFRESH_TOKEN}"
            ),
        )
        client = KFinanceApiClient(refresh_token=REFRESH_TOKEN)

        with pytest.raises(requests.ConnectionError) as exc_info:
            client._get_access_token_via_refresh_token()  # noqa: SLF001

        assert REFRESH_TOKEN not in str(exc_info.value)
