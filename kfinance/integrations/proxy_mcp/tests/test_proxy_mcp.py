from typing import Any

from pydantic import ValidationError
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kfinance.integrations.proxy_mcp.security import InboundAccessMiddleware
from kfinance.integrations.proxy_mcp.settings import InboundSettings


TOKEN = "s3cret-inbound-token"
TRUSTED_ORIGIN = "https://console.acme.example"
EVIL_ORIGIN = "https://evil.example"


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _guarded_app(allowed_origins: tuple[str, ...] = ()) -> TestClient:
    app = Starlette(routes=[Route("/health", _ok), Route("/mcp", _ok, methods=["GET", "POST"])])
    app.add_middleware(InboundAccessMiddleware, token=TOKEN, allowed_origins=allowed_origins)
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


class TestInboundSettings:
    def test_wildcard_origin_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            InboundSettings(allowed_origins=["*"])

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

        def _create(token: str | None, origins: list[str]) -> TestClient:
            monkeypatch.setattr(
                proxy_mcp.settings,
                "inbound",
                InboundSettings(token=token, allowed_origins=origins),
            )
            return TestClient(proxy_mcp.create_app())

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
