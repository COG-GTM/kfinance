from typing import Any

import pytest

from kfinance.integrations.local_mcp.http_security import (
    LOOPBACK_HOSTNAMES,
    LocalMcpSecurityMiddleware,
)


TOKEN = "test-token"


async def app(scope: dict, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def call(headers: dict[str, str]) -> int:
    middleware = LocalMcpSecurityMiddleware(
        app=app, auth_token=TOKEN, allowed_hosts=LOOPBACK_HOSTNAMES
    )
    scope = {
        "type": "http",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }
    statuses = []

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(scope, receive, send)
    return statuses[0]


class TestLocalMcpSecurityMiddleware:
    @pytest.mark.asyncio
    async def test_valid_token_is_accepted(self) -> None:
        status = await call({"host": "127.0.0.1:8000", "authorization": f"Bearer {TOKEN}"})
        assert status == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "authorization", ["", "Bearer wrong-token", TOKEN, "Basic dXNlcjpwYXNz"]
    )
    async def test_invalid_token_is_rejected(self, authorization: str) -> None:
        status = await call({"host": "127.0.0.1:8000", "authorization": authorization})
        assert status == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "headers",
        [
            {"host": "evil.example.com"},
            {"host": "127.0.0.1:8000", "origin": "https://evil.example.com"},
            {"host": "127.0.0.1:8000", "referer": "https://evil.example.com/page"},
        ],
    )
    async def test_rebinding_and_cross_origin_requests_are_rejected(
        self, headers: dict[str, str]
    ) -> None:
        status = await call({**headers, "authorization": f"Bearer {TOKEN}"})
        assert status == 403

    @pytest.mark.asyncio
    async def test_loopback_origin_is_accepted(self) -> None:
        status = await call(
            {
                "host": "localhost:8000",
                "origin": "http://localhost:8000",
                "authorization": f"Bearer {TOKEN}",
            }
        )
        assert status == 200
