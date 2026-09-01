from contextlib import asynccontextmanager
from typing import Any

from fastapi.testclient import TestClient
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from kfinance.integrations.proxy_mcp import proxy_mcp
from kfinance.integrations.proxy_mcp.settings import settings


@asynccontextmanager
async def lifespan(app: Starlette) -> Any:
    yield


async def mcp_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


class FakeHTTPApp:
    lifespan = staticmethod(lifespan)

    def __init__(self) -> None:
        self.app = Starlette(
            routes=[Route("/mcp", mcp_endpoint, methods=["POST"])],
            lifespan=lifespan,
        )

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self.app(scope, receive, send)


class FakeProxy:
    def http_app(self, *, path: str, transport: str) -> FakeHTTPApp:
        assert path == "/mcp"
        assert transport == "streamable-http"
        return FakeHTTPApp()


@pytest.fixture
def fake_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_mcp, "build_proxy", lambda: FakeProxy())


@pytest.fixture
def configure_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.client, "api_key", "test-client-key")
    monkeypatch.setattr(settings.client, "allowed_origins", [])


def test_create_app_requires_client_api_key(
    fake_proxy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.client, "api_key", None)

    with pytest.raises(ValueError, match="CLIENT_API_KEY must be set"):
        proxy_mcp.create_app()


def test_create_app_rejects_wildcard_origin(
    fake_proxy: None, configure_client: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.client, "allowed_origins", ["https://app.example.com", "*"])

    with pytest.raises(ValueError, match="Wildcard CORS origins are not allowed"):
        proxy_mcp.create_app()


def test_health_is_unauthenticated(fake_proxy: None, configure_client: None) -> None:
    with TestClient(proxy_mcp.create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.parametrize(
    ("authorization", "status_code"),
    [(None, 401), ("Bearer wrong-key", 401), ("Bearer test-client-key", 200)],
)
def test_mcp_requires_correct_client_key(
    fake_proxy: None,
    configure_client: None,
    authorization: str | None,
    status_code: int,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    with TestClient(proxy_mcp.create_app()) as client:
        response = client.post("/mcp", headers=headers)

    assert response.status_code == status_code
    if status_code == 401:
        assert response.json() == {"detail": "Unauthorized"}
        assert response.headers["WWW-Authenticate"] == "Bearer"
    else:
        assert response.json() == {"ok": True}


def test_cors_allows_configured_origin_without_credentials(
    fake_proxy: None, configure_client: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.client, "allowed_origins", ["https://app.example.com"])

    with TestClient(proxy_mcp.create_app()) as client:
        allowed = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer test-client-key",
                "Origin": "https://app.example.com",
            },
        )
        denied = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer test-client-key",
                "Origin": "https://evil.example",
            },
        )

    assert allowed.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in denied.headers


def test_cors_preflight_runs_before_auth(
    fake_proxy: None, configure_client: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.client, "allowed_origins", ["https://app.example.com"])

    with TestClient(proxy_mcp.create_app()) as client:
        response = client.options(
            "/mcp",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


def test_empty_allowed_origins_disables_cors(fake_proxy: None, configure_client: None) -> None:
    with TestClient(proxy_mcp.create_app()) as client:
        response = client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer test-client-key",
                "Origin": "https://app.example.com",
            },
        )

    assert "access-control-allow-origin" not in response.headers
