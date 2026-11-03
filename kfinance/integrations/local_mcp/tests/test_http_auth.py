import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kfinance.integrations.local_mcp.http_auth import (
    BearerAuthMiddleware,
    allowed_hosts_for,
)


TOKEN = "t" * 40


def build_client(bind_host: str = "127.0.0.1") -> TestClient:
    async def ok(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])])
    return TestClient(
        BearerAuthMiddleware(app, auth_token=TOKEN, allowed_hosts=allowed_hosts_for(bind_host)),
        base_url="http://127.0.0.1:8000",
    )


class TestBearerAuthMiddleware:
    def test_valid_token_is_accepted(self) -> None:
        """A request with the configured bearer token reaches the app."""
        resp = build_client().post("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_bearer_scheme_is_case_insensitive(self) -> None:
        """The auth scheme is matched case-insensitively per RFC 7235."""
        resp = build_client().post("/mcp", headers={"Authorization": f"bearer {TOKEN}"})
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "authorization",
        [None, "", f"Basic {TOKEN}", "Bearer wrong", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}"],
    )
    def test_missing_or_wrong_token_is_rejected(self, authorization: str | None) -> None:
        """Requests without the exact bearer token get a 401 and never reach the app."""
        headers = {} if authorization is None else {"Authorization": authorization}
        resp = build_client().post("/mcp", headers=headers)
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"] == 'Bearer realm="kfinance-mcp"'
        assert resp.text != "ok"

    @pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8000", "10.0.0.5"])
    def test_foreign_host_header_is_rejected(self, host: str) -> None:
        """DNS-rebinding requests carry the attacker's hostname and are rejected."""
        resp = build_client().post(
            "/mcp", headers={"Host": host, "Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1", "[::1]:8000"])
    def test_loopback_host_header_is_accepted(self, host: str) -> None:
        """Loopback Host headers are accepted for a loopback-bound server."""
        resp = build_client().post(
            "/mcp", headers={"Host": host, "Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 200

    def test_foreign_origin_is_rejected(self) -> None:
        """Cross-site browser requests are rejected even with a valid token."""
        resp = build_client().post(
            "/mcp",
            headers={"Origin": "https://evil.example", "Authorization": f"Bearer {TOKEN}"},
        )
        assert resp.status_code == 403

    def test_loopback_origin_is_accepted(self) -> None:
        """Local browser-based clients such as the MCP Inspector are accepted."""
        resp = build_client().post(
            "/mcp",
            headers={"Origin": "http://localhost:6274", "Authorization": f"Bearer {TOKEN}"},
        )
        assert resp.status_code == 200

    def test_explicit_bind_host_is_allowed(self) -> None:
        """A server bound to a specific address also accepts that address as Host."""
        resp = build_client(bind_host="192.168.1.20").post(
            "/mcp", headers={"Host": "192.168.1.20:8000", "Authorization": f"Bearer {TOKEN}"}
        )
        assert resp.status_code == 200

    def test_short_token_is_refused(self) -> None:
        """Weak tokens are refused at startup."""
        with pytest.raises(ValueError, match="at least 32"):
            BearerAuthMiddleware(lambda *_: None, auth_token="short", allowed_hosts=[])  # type: ignore[arg-type,return-value]


@pytest.mark.parametrize(
    ("bind_host", "expected"),
    [
        ("127.0.0.1", {"127.0.0.1", "localhost", "::1"}),
        ("0.0.0.0", {"127.0.0.1", "localhost", "::1"}),
        ("::", {"127.0.0.1", "localhost", "::1"}),
        ("Example.Internal", {"127.0.0.1", "localhost", "::1", "example.internal"}),
    ],
)
def test_allowed_hosts_for(bind_host: str, expected: set[str]) -> None:
    """Wildcard binds only accept loopback names; explicit binds also accept themselves."""
    assert allowed_hosts_for(bind_host) == expected
