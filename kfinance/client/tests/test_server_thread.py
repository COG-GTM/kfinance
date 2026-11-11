from functools import partial
from http.client import HTTPConnection
import json
import socket
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from kfinance.client.server_thread import (
    MAX_CONTENT_LENGTH,
    ServerThread,
    WebRequestHandler,
    origin_from_url,
)


LOGIN_ORIGIN = "https://kfinance.kensho.com"


@pytest.fixture
def server_thread():  # type: ignore[no-untyped-def]
    thread = ServerThread(allowed_origin=LOGIN_ORIGIN)
    thread.start()
    yield thread
    if thread.is_alive():
        thread.refresh_token = "teardown"
        _request(thread, "GET", "/")
    thread.join(timeout=5)


def _request(
    thread: ServerThread,
    method: str,
    path: str = "/",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str]]:
    conn = HTTPConnection("127.0.0.1", thread.server_port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}
    finally:
        conn.close()


def _post(thread: ServerThread, payload: Any, origin: str | None = LOGIN_ORIGIN) -> int:
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    status, _ = _request(thread, "POST", body=json.dumps(payload).encode(), headers=headers)
    return status


def test_origin_from_url() -> None:
    assert origin_from_url("https://kfinance.kensho.com") == LOGIN_ORIGIN
    assert origin_from_url("https://KFinance.Kensho.com/some/path?x=1") == LOGIN_ORIGIN
    assert origin_from_url("http://localhost:8000/") == "http://localhost:8000"
    assert origin_from_url("https://kfinance.kensho.com:443/") == LOGIN_ORIGIN
    assert origin_from_url("http://example.com:80") == "http://example.com"
    assert origin_from_url("https://example.com:8443") == "https://example.com:8443"
    assert origin_from_url("http://user:pw@Example.com:8080/x") == "http://example.com:8080"
    assert origin_from_url("http://[::1]:8000") == "http://[::1]:8000"


def test_state_is_random_per_login() -> None:
    first = ServerThread(allowed_origin=LOGIN_ORIGIN)
    second = ServerThread(allowed_origin=LOGIN_ORIGIN)
    try:
        assert len(first.state) >= 32
        assert first.state != second.state
    finally:
        first.server.server_close()
        second.server.server_close()


def test_valid_post_completes_login(server_thread: ServerThread) -> None:
    status = _post(server_thread, {"refresh_token": "alice", "state": server_thread.state})
    assert status == 200
    server_thread.join(timeout=5)
    assert not server_thread.is_alive()
    assert server_thread.refresh_token == "alice"


@pytest.mark.parametrize(
    "payload, origin, expected_status",
    [
        pytest.param({"refresh_token": "mallory"}, LOGIN_ORIGIN, 403, id="missing state"),
        pytest.param(
            {"refresh_token": "mallory", "state": "guess"}, LOGIN_ORIGIN, 403, id="wrong state"
        ),
        pytest.param(
            {"refresh_token": "mallory", "state": 1}, LOGIN_ORIGIN, 403, id="non-string state"
        ),
        pytest.param({"refresh_token": "mallory"}, "https://evil.example", 403, id="other origin"),
        pytest.param({"refresh_token": "mallory"}, None, 403, id="no origin"),
        pytest.param({"refresh_token": "mallory"}, "null", 403, id="null origin"),
        pytest.param(["refresh_token"], LOGIN_ORIGIN, 400, id="non-object body"),
    ],
)
def test_rejected_posts_keep_server_waiting(
    server_thread: ServerThread, payload: Any, origin: str | None, expected_status: int
) -> None:
    assert _post(server_thread, payload, origin=origin) == expected_status
    assert server_thread.is_alive()
    assert server_thread.refresh_token is None

    # the legitimate login page can still complete the login afterwards
    assert _post(server_thread, {"refresh_token": "alice", "state": server_thread.state}) == 200
    server_thread.join(timeout=5)
    assert server_thread.refresh_token == "alice"


def test_correct_state_from_other_origin_is_rejected(server_thread: ServerThread) -> None:
    payload = {"refresh_token": "mallory", "state": server_thread.state}
    assert _post(server_thread, payload, origin="https://evil.example") == 403
    assert server_thread.is_alive()
    assert server_thread.refresh_token is None


@pytest.mark.parametrize("refresh_token", ["", None, 123])
def test_empty_or_invalid_refresh_token_is_rejected(
    server_thread: ServerThread, refresh_token: Any
) -> None:
    payload = {"refresh_token": refresh_token, "state": server_thread.state}
    assert _post(server_thread, payload) == 400
    assert server_thread.is_alive()
    assert server_thread.refresh_token is None


def test_malformed_json_is_rejected(server_thread: ServerThread) -> None:
    status, _ = _request(server_thread, "POST", body=b"{not json", headers={"Origin": LOGIN_ORIGIN})
    assert status == 400
    assert server_thread.is_alive()


def test_oversized_body_is_rejected_without_reading(server_thread: ServerThread) -> None:
    conn = HTTPConnection("127.0.0.1", server_thread.server_port, timeout=5)
    try:
        conn.putrequest("POST", "/")
        conn.putheader("Origin", LOGIN_ORIGIN)
        conn.putheader("Content-Length", str(MAX_CONTENT_LENGTH + 1))
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 413
    finally:
        conn.close()
    assert server_thread.is_alive()
    assert server_thread.refresh_token is None


def test_missing_content_length_is_rejected(server_thread: ServerThread) -> None:
    conn = HTTPConnection("127.0.0.1", server_thread.server_port, timeout=5)
    try:
        conn.putrequest("POST", "/")
        conn.putheader("Origin", LOGIN_ORIGIN)
        conn.endheaders()
        assert conn.getresponse().status == 411
    finally:
        conn.close()
    assert server_thread.is_alive()


def test_preflight_only_allows_login_origin(server_thread: ServerThread) -> None:
    status, headers = _request(
        server_thread,
        "OPTIONS",
        headers={
            "Origin": LOGIN_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-requested-with",
        },
    )
    assert status == 204
    assert headers["access-control-allow-origin"] == LOGIN_ORIGIN
    allowed_headers = {
        h.strip().lower() for h in headers["access-control-allow-headers"].split(",")
    }
    assert {"content-type", "x-requested-with"} <= allowed_headers
    assert "access-control-allow-credentials" not in headers

    status, headers = _request(
        server_thread,
        "OPTIONS",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert status == 403
    assert "access-control-allow-origin" not in headers


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_get_and_head_return_empty_200(server_thread: ServerThread, method: str) -> None:
    status, headers = _request(server_thread, method, "/etc/passwd")
    assert status == 200
    assert headers["content-length"] == "0"
    assert "access-control-allow-origin" not in headers
    assert server_thread.is_alive()


def test_client_browser_login_passes_state_and_origin() -> None:
    from kfinance.client.kfinance import Client

    opened_urls: list[str] = []

    def fake_open(url: str) -> bool:
        opened_urls.append(url)
        query = parse_qs(urlsplit(url).query)
        thread_port = int(query["port"][0])
        state = query["state"][0]
        conn = HTTPConnection("127.0.0.1", thread_port, timeout=5)
        try:
            # an attacker without the state is rejected first, then the login page succeeds
            for body in (
                {"refresh_token": "mallory"},
                {"refresh_token": "alice", "state": state},
            ):
                conn.request(
                    "POST",
                    "/",
                    body=json.dumps(body).encode(),
                    headers={"Origin": "https://api.example.com"},
                )
                conn.getresponse().read()
        finally:
            conn.close()
        return True

    with (
        patch("kfinance.client.kfinance.webbrowser.open", side_effect=fake_open),
        patch("kfinance.client.kfinance.KFinanceApiClient") as api_client,
    ):
        Client(api_host="https://api.example.com")

    assert len(opened_urls) == 1
    assert opened_urls[0].startswith("https://api.example.com/automated_login?")
    assert api_client.call_args.kwargs["refresh_token"] == "alice"


def test_foreign_origin_post_to_root_leaves_token_unset(server_thread: ServerThread) -> None:
    status, headers = _request(
        server_thread,
        "POST",
        "/",
        body=json.dumps({"refresh_token": "x"}).encode(),
        headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
    )
    assert status == 403
    assert "access-control-allow-origin" not in headers
    assert server_thread.is_alive()
    assert server_thread.refresh_token is None


def test_server_stops_waiting_after_login_timeout() -> None:
    thread = ServerThread(allowed_origin=LOGIN_ORIGIN, login_timeout=0.5)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert thread.refresh_token is None


def test_stalled_connection_does_not_block_login(server_thread: ServerThread) -> None:
    with patch.object(WebRequestHandler, "timeout", 0.5):
        stalled = socket.create_connection(("127.0.0.1", server_thread.server_port), timeout=5)
        try:
            stalled.sendall(
                f"POST / HTTP/1.1\r\nOrigin: {LOGIN_ORIGIN}\r\nContent-Length: 100\r\n\r\n".encode()
            )
            payload = {"refresh_token": "alice", "state": server_thread.state}
            assert _post(server_thread, payload) == 200
        finally:
            stalled.close()
    server_thread.join(timeout=5)
    assert server_thread.refresh_token == "alice"


def test_client_browser_login_times_out() -> None:
    from kfinance.client.kfinance import Client

    with (
        patch("kfinance.client.kfinance.ServerThread", partial(ServerThread, login_timeout=0.5)),
        patch("kfinance.client.kfinance.webbrowser.open", return_value=True),
        patch("kfinance.client.kfinance.KFinanceApiClient") as api_client,
        pytest.raises(TimeoutError),
    ):
        Client(api_host="https://api.example.com")
    api_client.assert_not_called()
