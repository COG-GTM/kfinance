from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from kfinance.integrations.proxy_mcp.proxy_mcp import add_security_middleware
from kfinance.integrations.proxy_mcp.settings import settings


def build_test_client() -> TestClient:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    add_security_middleware(app)
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def allowed_origins() -> Iterator[list[str]]:
    original = settings.allowed_origins
    yield settings.allowed_origins
    settings.allowed_origins = original


class TestSecurityMiddleware:
    def test_same_origin_request_allowed(self, allowed_origins: list[str]) -> None:
        response = build_test_client().get("/health")
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

    def test_unknown_origin_rejected(self, allowed_origins: list[str]) -> None:
        response = build_test_client().get("/health", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers

    def test_unknown_origin_preflight_rejected(self, allowed_origins: list[str]) -> None:
        settings.allowed_origins = ["https://trusted.example"]
        response = build_test_client().options(
            "/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in response.headers

    def test_allowed_origin_permitted_without_credentials(self, allowed_origins: list[str]) -> None:
        settings.allowed_origins = ["https://trusted.example"]
        response = build_test_client().get("/health", headers={"Origin": "https://trusted.example"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://trusted.example"
        assert "access-control-allow-credentials" not in response.headers

    def test_untrusted_host_rejected(self, allowed_origins: list[str]) -> None:
        client = build_test_client()
        response = client.get("/health", headers={"Host": "attacker.example"})
        assert response.status_code == 400
