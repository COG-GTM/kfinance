import logging

import pytest

from kfinance.client.auth_errors import install_refresh_token_log_filter, redact_refresh_token


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "GET https://host/oauth2/refresh?refresh_token=abc.def-123 503",
            "GET https://host/oauth2/refresh?refresh_token=REDACTED 503",
        ),
        ("/refresh?a=1&refresh_token=abc&b=2", "/refresh?a=1&refresh_token=REDACTED&b=2"),
        ("no secrets here", "no secrets here"),
    ],
)
def test_redact_refresh_token(text: str, expected: str) -> None:
    assert redact_refresh_token(text) == expected


def test_http_library_log_records_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """
    GIVEN the refresh token log filter is installed
    WHEN an HTTP library logs a URL containing a refresh token
    THEN the logged message contains a placeholder instead of the token.
    """
    install_refresh_token_log_filter()
    install_refresh_token_log_filter()
    assert len(logging.getLogger("httpx").filters) == 1

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("urllib3.connectionpool").debug(
            '%s "%s %s HTTP/1.1" %s', "https://host:443", "GET", "/refresh?refresh_token=abc", 503
        )
    assert "abc" not in caplog.text
    assert "refresh_token=REDACTED" in caplog.text
