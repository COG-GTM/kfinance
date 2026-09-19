from http import HTTPStatus
import json
import secrets
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlsplit


Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]

DEFAULT_HOST = "127.0.0.1"
AUTH_TOKEN_ENV_VAR = "KFINANCE_MCP_AUTH_TOKEN"
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def generate_auth_token() -> str:
    """Generate a random bearer token for inbound MCP clients."""
    return secrets.token_urlsafe(32)


def _hostname(value: str) -> str:
    """Extract the lower-cased hostname from a Host header or an Origin/Referer URL."""
    if "//" in value:
        value = urlsplit(value).netloc
    value = value.strip().lower()
    # Strip the port, keeping bracketed IPv6 literals intact.
    if value.startswith("["):
        return value.partition("]")[0] + "]"
    return value.partition(":")[0]


class LocalMcpSecurityMiddleware:
    """ASGI middleware that authenticates inbound MCP clients.

    The local MCP server holds a single upstream Kensho credential that is used for
    every tool call, so any client reaching the transport would otherwise act with the
    operator's entitlements. This middleware requires a bearer token on every request
    and rejects requests whose Host or Origin header points at a host that was not
    explicitly allowed, which blocks DNS-rebinding and cross-origin browser attacks.
    """

    def __init__(self, app: AsgiApp, auth_token: str, allowed_hosts: Iterable[str]) -> None:
        """Wrap an ASGI app with bearer auth and Host/Origin validation.

        :param app: The ASGI app to protect.
        :param auth_token: The bearer token that inbound clients must present.
        :param allowed_hosts: Hostnames accepted in the Host, Origin and Referer headers.
        """
        self._app = app
        self._auth_token = auth_token
        self._allowed_hosts = frozenset(host.lower() for host in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject unauthenticated or cross-origin requests, then delegate to the app."""
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

        for header_name in ("host", "origin", "referer"):
            header_value = headers.get(header_name)
            if header_value and _hostname(header_value) not in self._allowed_hosts:
                await self._deny(send, HTTPStatus.FORBIDDEN, f"Rejected {header_name} header")
                return

        authorization = headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, self._auth_token):
            await self._deny(send, HTTPStatus.UNAUTHORIZED, "Invalid or missing bearer token")
            return

        await self._app(scope, receive, send)

    @staticmethod
    async def _deny(send: Send, status: HTTPStatus, detail: str) -> None:
        body = json.dumps({"error": detail}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if status == HTTPStatus.UNAUTHORIZED:
            headers.append((b"www-authenticate", b'Bearer realm="kfinance-mcp"'))
        await send({"type": "http.response.start", "status": int(status), "headers": headers})
        await send({"type": "http.response.body", "body": body})
