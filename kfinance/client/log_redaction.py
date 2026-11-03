import logging
import re


REDACTED = "[REDACTED]"

# Loggers that record outgoing request URLs (including the query string).
URL_LOGGING_LOGGERS = ("httpx", "urllib3.connectionpool")

_REFRESH_TOKEN_PATTERN = re.compile(r"(refresh(?:_|%5F)token=)[^&\s'\"]+", re.IGNORECASE)


def redact_refresh_token(text: str) -> str:
    """Replace the value of any ``refresh_token=`` query parameter in text with a placeholder."""
    return _REFRESH_TOKEN_PATTERN.sub(rf"\g<1>{REDACTED}", text)


class RefreshTokenRedactingFilter(logging.Filter):
    """Logging filter that removes refresh token values from formatted log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record in place so that it no longer contains a refresh token."""
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        redacted = redact_refresh_token(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_FILTER = RefreshTokenRedactingFilter()


def install_refresh_token_log_filter() -> None:
    """Attach the refresh token redacting filter to the HTTP client loggers (idempotent)."""
    for name in URL_LOGGING_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RefreshTokenRedactingFilter) for f in logger.filters):
            logger.addFilter(_FILTER)
