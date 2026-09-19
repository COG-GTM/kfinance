import pytest


# The proxy MCP skeleton depends on fastapi/fastmcp, which are not core kfinance dependencies.
pytest.importorskip("fastapi")
pytest.importorskip("fastmcp")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from kfinance.integrations.proxy_mcp.proxy_mcp import (  # noqa: E402
    InboundBearerTokenMiddleware,
    create_app,
)
from kfinance.integrations.proxy_mcp.settings import settings  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(InboundBearerTokenMiddleware, token="expected-token")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    @app.post("/mcp")
    async def mcp() -> dict:
        return {"ok": True}

    return TestClient(app)


class TestInboundBearerTokenMiddleware:
    def test_health_does_not_require_a_token(self, client: TestClient) -> None:
        assert client.get("/health").status_code == 200

    def test_request_without_token_is_rejected(self, client: TestClient) -> None:
        assert client.post("/mcp").status_code == 401

    def test_request_with_wrong_token_is_rejected(self, client: TestClient) -> None:
        response = client.post("/mcp", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401

    def test_request_with_expected_token_is_forwarded(self, client: TestClient) -> None:
        response = client.post("/mcp", headers={"Authorization": "Bearer expected-token"})
        assert response.status_code == 200


ALLOWED_ORIGIN = "https://app.example.com"


@pytest.fixture
def configured_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(settings.inbound, "token", "expected-token")
    monkeypatch.setattr(settings.inbound, "allowed_origins", [ALLOWED_ORIGIN])
    monkeypatch.setattr(settings.auth, "refresh_token", "fake-refresh-token")
    return create_app()


class TestCreateApp:
    def test_create_app_requires_an_inbound_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings.inbound, "token", None)
        with pytest.raises(ValueError, match="INBOUND_TOKEN"):
            create_app()

    def test_unauthenticated_request_is_rejected_with_cors_headers(
        self, configured_app: FastAPI
    ) -> None:
        # CORS wraps the auth middleware so an allowed browser can read the 401 instead of
        # seeing an opaque network error.
        with TestClient(configured_app) as client:
            response = client.post("/mcp", headers={"Origin": ALLOWED_ORIGIN})
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

    def test_disallowed_origin_gets_no_cors_headers(self, configured_app: FastAPI) -> None:
        with TestClient(configured_app) as client:
            response = client.post("/mcp", headers={"Origin": "https://evil.example.com"})
        assert response.status_code == 401
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.parametrize("header", ["authorization", "mcp-session-id", "last-event-id"])
    def test_preflight_allows_mcp_headers_from_allowed_origin(
        self, configured_app: FastAPI, header: str
    ) -> None:
        with TestClient(configured_app) as client:
            response = client.options(
                "/mcp",
                headers={
                    "Origin": ALLOWED_ORIGIN,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": header,
                },
            )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
        assert header in response.headers["access-control-allow-headers"].lower()
