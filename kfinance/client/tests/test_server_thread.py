from http.client import HTTPConnection
import json

import pytest

from kfinance.client.server_thread import MAX_CONTENT_LENGTH, ServerThread, origin_from_url


ORIGIN = "https://kfinance.kensho.com"


@pytest.fixture
def server_thread():  # type: ignore[no-untyped-def]
    thread = ServerThread(allowed_origin=ORIGIN)
    thread.start()
    yield thread
    if thread.is_alive():
        _request(thread, "POST", {"state": thread.state, "refresh_token": "shutdown"})
        thread.join(timeout=5)


def _request(  # type: ignore[no-untyped-def]
    thread: ServerThread,
    method: str,
    body: object = None,
    origin: str | None = ORIGIN,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
):
    conn = HTTPConnection("127.0.0.1", thread.server_port, timeout=5)
    request_headers = {"Content-Type": "application/json"}
    if origin is not None:
        request_headers["Origin"] = origin
    request_headers.update(headers or {})
    payload = (
        raw_body
        if raw_body is not None
        else (json.dumps(body).encode() if body is not None else None)
    )
    conn.request(method, "/", body=payload, headers=request_headers)
    response = conn.getresponse()
    response.read()
    conn.close()
    return response


def test_origin_from_url() -> None:
    assert origin_from_url("https://kfinance.kensho.com/") == ORIGIN
    assert origin_from_url("http://localhost:8000/api") == "http://localhost:8000"


def test_state_is_random() -> None:
    first, second = ServerThread(ORIGIN), ServerThread(ORIGIN)
    try:
        assert first.state != second.state
        assert len(first.state) >= 32
    finally:
        first.server.server_close()
        second.server.server_close()


def test_valid_post_sets_token(server_thread: ServerThread) -> None:
    response = _request(
        server_thread, "POST", {"state": server_thread.state, "refresh_token": "good"}
    )
    assert response.status == 200
    assert response.getheader("Access-Control-Allow-Origin") == ORIGIN
    assert response.getheader("Access-Control-Allow-Credentials") is None
    server_thread.join(timeout=5)
    assert server_thread.refresh_token == "good"


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "null", "https://kfinance.kensho.com.evil.example"]
)
def test_post_from_wrong_origin_rejected(server_thread: ServerThread, origin: str | None) -> None:
    response = _request(
        server_thread,
        "POST",
        {"state": server_thread.state, "refresh_token": "attacker"},
        origin=origin,
    )
    assert response.status == 403
    assert response.getheader("Access-Control-Allow-Origin") is None
    assert server_thread.refresh_token is None


@pytest.mark.parametrize("state", [None, "", "wrong", 123])
def test_post_with_bad_state_rejected(server_thread: ServerThread, state: object) -> None:
    body = {"refresh_token": "attacker"}
    if state is not None:
        body["state"] = state  # type: ignore[assignment]
    response = _request(server_thread, "POST", body)
    assert response.status == 403
    assert server_thread.refresh_token is None


@pytest.mark.parametrize(
    "raw_body", [b"not json", b"\xff\xfe", b"[1, 2]", b'"str"', json.dumps({}).encode()]
)
def test_post_with_malformed_body_rejected(server_thread: ServerThread, raw_body: bytes) -> None:
    response = _request(server_thread, "POST", raw_body=raw_body)
    assert response.status in (400, 403)
    assert server_thread.refresh_token is None


@pytest.mark.parametrize("refresh_token", [None, "", 123, ["a"]])
def test_post_with_invalid_token_rejected(
    server_thread: ServerThread, refresh_token: object
) -> None:
    response = _request(
        server_thread, "POST", {"state": server_thread.state, "refresh_token": refresh_token}
    )
    assert response.status == 400
    assert server_thread.refresh_token is None


def test_post_with_oversized_content_length_rejected(server_thread: ServerThread) -> None:
    response = _request(
        server_thread,
        "POST",
        raw_body=b"{}",
        headers={"Content-Length": str(MAX_CONTENT_LENGTH + 1)},
    )
    assert response.status == 413
    assert server_thread.refresh_token is None


def test_post_with_invalid_content_length_rejected(server_thread: ServerThread) -> None:
    response = _request(server_thread, "POST", raw_body=b"{}", headers={"Content-Length": "abc"})
    assert response.status == 411
    assert server_thread.refresh_token is None


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_get_and_head_do_not_serve_files(server_thread: ServerThread, method: str) -> None:
    response = _request(server_thread, method)
    assert response.status == 405


def test_unsupported_method_rejected(server_thread: ServerThread) -> None:
    assert _request(server_thread, "PUT", {}).status == 501


def test_preflight_only_allows_expected_origin(server_thread: ServerThread) -> None:
    allowed = _request(server_thread, "OPTIONS")
    assert allowed.status == 204
    assert allowed.getheader("Access-Control-Allow-Origin") == ORIGIN
    denied = _request(server_thread, "OPTIONS", origin="https://evil.example")
    assert denied.status == 403
    assert denied.getheader("Access-Control-Allow-Origin") is None
