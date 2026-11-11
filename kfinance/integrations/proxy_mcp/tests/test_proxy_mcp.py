from typing import Any

from pydantic import ValidationError
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kfinance.integrations.proxy_mcp.security import (
    InboundAccessMiddleware,
    host_header_name,
    is_loopback,
)
from kfinance.integrations.proxy_mcp.settings import InboundSettings


TOKEN = "s3cret-inbound-token"
TRUSTED_ORIGIN = "https://console.acme.example"
EVIL_ORIGIN = "https://evil.example"


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _guarded_app(
    allowed_origins: tuple[str, ...] = (), allowed_hosts: tuple[str, ...] = ()
) -> TestClient:
    app = Starlette(routes=[Route("/health", _ok), Route("/mcp", _ok, methods=["GET", "POST"])])
    app.add_middleware(
        InboundAccessMiddleware,
        token=TOKEN,
        allowed_origins=allowed_origins,
        allowed_hosts=allowed_hosts,
    )
    return TestClient(app)


class TestInboundAccessMiddleware:
    def test_missing_token_is_rejected(self) -> None:
        response = _guarded_app().post("/mcp", json={})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        "header",
        [
            f"Bearer {TOKEN}x",
            f"Basic {TOKEN}",
            TOKEN,
            "Bearer ",
            "Bearer ünïcode".encode("latin-1"),
        ],
    )
    def test_wrong_token_is_rejected(self, header: str | bytes) -> None:
        response = _guarded_app().post("/mcp", json={}, headers={"Authorization": header})
        assert response.status_code == 401

    def test_correct_token_is_accepted(self) -> None:
        response = _guarded_app().post(
            "/mcp", json={}, headers={"Authorization": f"bearer {TOKEN}"}
        )
        assert response.status_code == 200

    def test_health_is_unauthenticated(self) -> None:
        assert _guarded_app().get("/health").status_code == 200

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
    def test_health_exemption_is_get_only(self, method: str) -> None:
        assert _guarded_app().request(method, "/health").status_code == 401

    def test_untrusted_origin_is_rejected_even_with_token(self) -> None:
        response = _guarded_app((TRUSTED_ORIGIN,)).post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {TOKEN}", "Origin": EVIL_ORIGIN},
        )
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers

    def test_trusted_origin_with_token_is_accepted(self) -> None:
        response = _guarded_app((TRUSTED_ORIGIN,)).post(
            "/mcp",
            json={},
            headers={"Authorization": f"Bearer {TOKEN}", "Origin": TRUSTED_ORIGIN},
        )
        assert response.status_code == 200

    def test_empty_token_is_refused(self) -> None:
        with pytest.raises(ValueError):
            InboundAccessMiddleware(_ok, token="")  # type: ignore[arg-type]

    @pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.2"])
    def test_unknown_host_is_rejected_even_with_token(self, host: str) -> None:
        client = _guarded_app(allowed_hosts=("127.0.0.1", "localhost"))
        response = client.post(
            "/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}", "Host": host}
        )
        assert response.status_code == 421
        assert client.get("/health", headers={"Host": host}).status_code == 421

    @pytest.mark.parametrize("host", ["127.0.0.1:8000", "LOCALHOST", "[::1]:8000"])
    def test_allowed_host_with_token_is_accepted(self, host: str) -> None:
        client = _guarded_app(allowed_hosts=("127.0.0.1", "localhost", "::1"))
        response = client.post(
            "/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}", "Host": host}
        )
        assert response.status_code == 200


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("Localhost", "localhost"),
        ("[::1]:8000", "::1"),
        ("[::1]", "::1"),
        ("proxy.acme.example:443", "proxy.acme.example"),
        ("", ""),
    ],
)
def test_host_header_name(header: str, name: str) -> None:
    assert host_header_name(header) == name


@pytest.mark.parametrize(
    ("host", "loopback"),
    [("127.0.0.1", True), ("::1", True), ("localhost", True), ("0.0.0.0", False), ("::", False)],
)
def test_is_loopback(host: str, loopback: bool) -> None:
    assert is_loopback(host) is loopback


class TestInboundSettings:
    def test_wildcard_origin_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            InboundSettings(allowed_origins=["*"])

    def test_wildcard_host_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            InboundSettings(allowed_hosts=["*"])

    def test_token_is_not_rendered(self) -> None:
        assert TOKEN not in repr(InboundSettings(token=TOKEN))


class TestCreateApp:
    @pytest.fixture
    def create_app(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        pytest.importorskip("fastapi")
        from fastmcp import FastMCP

        from kfinance.integrations.proxy_mcp import proxy_mcp

        backend = FastMCP("stub")

        @backend.tool
        def ping() -> str:
            return "pong"

        monkeypatch.setattr(proxy_mcp, "build_proxy", lambda: backend)

        def _create(
            token: str | None,
            origins: list[str],
            bind_host: str = "127.0.0.1",
            hosts: list[str] | None = None,
        ) -> TestClient:
            monkeypatch.setattr(
                proxy_mcp.settings,
                "inbound",
                InboundSettings(token=token, allowed_origins=origins, allowed_hosts=hosts or []),
            )
            return TestClient(
                proxy_mcp.create_app(bind_host=bind_host), base_url="http://127.0.0.1:8000"
            )

        return _create

    def test_requires_inbound_token(self, create_app: Any) -> None:
        with pytest.raises(ValueError, match="INBOUND_TOKEN"):
            create_app(None, [])

    def test_unauthenticated_cross_origin_post_is_rejected(self, create_app: Any) -> None:
        client = create_app(TOKEN, [])
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Origin": EVIL_ORIGIN},
        )
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers

    def test_unauthenticated_initialize_is_rejected(self, create_app: Any) -> None:
        client = create_app(TOKEN, [])
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 401

    def test_loopback_bind_rejects_rebound_host(self, create_app: Any) -> None:
        client = create_app(TOKEN, [])
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": f"Bearer {TOKEN}", "Host": "attacker.example:8000"},
        )
        assert response.status_code == 421

    def test_non_loopback_bind_uses_configured_hosts(self, create_app: Any) -> None:
        client = create_app(TOKEN, [], bind_host="0.0.0.0", hosts=["proxy.acme.example"])
        assert client.get("/health", headers={"Host": "proxy.acme.example"}).status_code == 200
        assert client.get("/health").status_code == 421

    def test_non_loopback_bind_without_hosts_still_requires_token(self, create_app: Any) -> None:
        client = create_app(TOKEN, [], bind_host="0.0.0.0")
        assert client.get("/health", headers={"Host": "10.0.0.5:8000"}).status_code == 200
        response = client.post("/mcp", json={}, headers={"Host": "10.0.0.5:8000"})
        assert response.status_code == 401

    def test_preflight_from_untrusted_origin_gets_no_cors_grant(self, create_app: Any) -> None:
        client = create_app(TOKEN, [TRUSTED_ORIGIN])
        response = client.options(
            "/mcp",
            headers={
                "Origin": EVIL_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.headers.get("access-control-allow-origin") != EVIL_ORIGIN
        assert "access-control-allow-credentials" not in response.headers

    def test_preflight_from_trusted_origin(self, create_app: Any) -> None:
        client = create_app(TOKEN, [TRUSTED_ORIGIN])
        response = client.options(
            "/mcp",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,mcp-session-id",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
        assert "access-control-allow-credentials" not in response.headers

    def test_missing_token_from_trusted_origin_is_401_with_cors(self, create_app: Any) -> None:
        client = create_app(TOKEN, [TRUSTED_ORIGIN])
        response = client.post("/mcp", json={}, headers={"Origin": TRUSTED_ORIGIN})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == TRUSTED_ORIGIN

    def test_authenticated_mcp_session(self, create_app: Any) -> None:
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json, text/event-stream",
        }
        with create_app(TOKEN, []) as client:
            assert client.get("/health").json() == {"status": "healthy"}
            init = client.post(
                "/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )
            assert init.status_code == 200
            assert "mcp-session-id" in init.headers
