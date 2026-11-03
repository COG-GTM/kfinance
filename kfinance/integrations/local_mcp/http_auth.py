from http import HTTPStatus
import json
import secrets
from typing import Any, Awaitable, Callable, Iterable, MutableMapping
from urllib.parse import urlsplit


Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

AUTH_TOKEN_ENV_VAR = "KFINANCE_MCP_AUTH_TOKEN"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MIN_AUTH_TOKEN_LENGTH = 32


def _hostname(value: str) -> str:
    """Return the lower-cased hostname of a Host header value or an Origin URL."""
    netloc = urlsplit(value).netloc if "://" in value else value
    netloc = netloc.strip().lower().rpartition("@")[2]
    if netloc.startswith("["):
        return netloc[1:].partition("]")[0]
    if netloc.count(":") > 1:
        # A bare IPv6 literal such as ::1 has no port.
        return netloc
    return netloc.partition(":")[0]


def allowed_hosts_for(bind_host: str) -> frozenset[str]:
    """Hostnames accepted in Host/Origin headers for a server bound to bind_host."""
    bind_host = _hostname(bind_host)
    if bind_host in {"", "0.0.0.0", "::"}:
        return LOOPBACK_HOSTS
    return LOOPBACK_HOSTS | {bind_host}


class BearerAuthMiddleware:
    """ASGI middleware that guards the local MCP network transports.

    Every tool on the local MCP server runs with the operator's kfinance credentials, so
    any client that can reach the port would otherwise act as the operator. Each HTTP
    request must carry `Authorization: Bearer <token>`, and its Host and Origin headers
    must name an allowed host, which blocks DNS-rebinding and cross-site browser requests.
    """

    def __init__(self, app: ASGIApp, auth_token: str, allowed_hosts: Iterable[str]) -> None:
        """Wrap an ASGI app.

        :param app: The ASGI app to protect.
        :param auth_token: The bearer token clients must present.
        :param allowed_hosts: Hostnames accepted in the Host and Origin headers.
        """
        if len(auth_token) < MIN_AUTH_TOKEN_LENGTH:
            raise ValueError(
                f"The MCP auth token must be at least {MIN_AUTH_TOKEN_LENGTH} characters long."
            )
        self.app = app
        self._expected_authorization = f"bearer {auth_token}".encode()
        self._allowed_hosts = frozenset(_hostname(host) for host in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject unauthenticated or cross-origin requests before they reach the app."""
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return

        headers: dict[str, str] = {}
        for raw_key, raw_value in scope.get("headers", []):
            headers.setdefault(raw_key.decode("latin-1").lower(), raw_value.decode("latin-1"))

        host = headers.get("host")
        if host is None or _hostname(host) not in self._allowed_hosts:
            await self._reject(send, HTTPStatus.FORBIDDEN, "Host header not allowed")
            return
        origin = headers.get("origin")
        if origin is not None and _hostname(origin) not in self._allowed_hosts:
            await self._reject(send, HTTPStatus.FORBIDDEN, "Origin header not allowed")
            return

        scheme, _, token = headers.get("authorization", "").partition(" ")
        presented = f"{scheme.lower()} {token.strip()}".encode()
        if not secrets.compare_digest(presented, self._expected_authorization):
            await self._reject(send, HTTPStatus.UNAUTHORIZED, "Missing or invalid bearer token")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send, status: HTTPStatus, detail: str) -> None:
        body = json.dumps({"error": detail}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if status == HTTPStatus.UNAUTHORIZED:
            headers.append((b"www-authenticate", b'Bearer realm="kfinance-mcp"'))
        await send({"type": "http.response.start", "status": int(status), "headers": headers})
        await send({"type": "http.response.body", "body": body})
