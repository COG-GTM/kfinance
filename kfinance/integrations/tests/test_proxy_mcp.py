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


class TestCreateApp:
    def test_create_app_requires_an_inbound_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings.inbound, "token", None)
        with pytest.raises(ValueError, match="INBOUND_TOKEN"):
            create_app()
