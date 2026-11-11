import logging
import re


_REFRESH_TOKEN_QUERY_PATTERN = re.compile(r"(refresh_token=)[^&\s'\"]+")
REDACTED = "REDACTED"

# Loggers of the HTTP libraries that record full request URLs.
_HTTP_LIBRARY_LOGGERS = ("httpx", "httpcore", "urllib3.connectionpool")


class KfinanceAuthenticationError(Exception):
    """Raised when an access token cannot be obtained.

    The message only carries the HTTP status code (or the error type for connection
    errors) so that credentials included in the token request never end up in
    exception text, tool errors, or logs.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Initialize with a credential-free message and optional HTTP status code."""
        super().__init__(message)
        self.status_code = status_code


def describe_request_failure(status_code: int | None, error: BaseException) -> str:
    """Return a credential-free description of a failed token request."""
    if status_code is not None:
        return f"HTTP {status_code}"
    return type(error).__name__


def redact_refresh_token(text: str) -> str:
    """Replace refresh_token query parameter values in text with a placeholder."""
    return _REFRESH_TOKEN_QUERY_PATTERN.sub(rf"\g<1>{REDACTED}", text)


class RefreshTokenRedactingFilter(logging.Filter):
    """Logging filter that redacts refresh_token query parameter values."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the formatted message of the record in place."""
        message = record.getMessage()
        redacted = redact_refresh_token(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


_refresh_token_filter = RefreshTokenRedactingFilter()


def install_refresh_token_log_filter() -> None:
    """Attach the refresh token redacting filter to HTTP library loggers (idempotent)."""
    for logger_name in _HTTP_LIBRARY_LOGGERS:
        logger = logging.getLogger(logger_name)
        if _refresh_token_filter not in logger.filters:
            logger.addFilter(_refresh_token_filter)
