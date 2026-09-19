from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from kfinance.integrations.proxy_mcp.proxy_mcp import HEALTH_PATH, ClientTokenAuthMiddleware


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(
        ClientTokenAuthMiddleware, tokens=["valid-token"], exempt_paths={HEALTH_PATH}
    )

    @app.get(HEALTH_PATH)
    async def health() -> dict:
        return {"status": "healthy"}

    @app.get("/mcp")
    async def mcp() -> dict:
        return {"status": "forwarded"}

    return TestClient(app)


class TestClientTokenAuthMiddleware:
    def test_request_without_token_is_rejected(self, client: TestClient) -> None:
        response = client.get("/mcp")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        "authorization", ["Bearer wrong-token", "valid-token", "Basic valid-token", "Bearer "]
    )
    def test_request_with_invalid_token_is_rejected(
        self, client: TestClient, authorization: str
    ) -> None:
        response = client.get("/mcp", headers={"Authorization": authorization})
        assert response.status_code == 401

    def test_request_with_valid_token_is_forwarded(self, client: TestClient) -> None:
        response = client.get("/mcp", headers={"Authorization": "Bearer valid-token"})
        assert response.status_code == 200
        assert response.json() == {"status": "forwarded"}

    def test_health_check_does_not_require_a_token(self, client: TestClient) -> None:
        response = client.get(HEALTH_PATH)
        assert response.status_code == 200
