from http.client import HTTPConnection
import json
from pathlib import Path

import pytest

from kfinance.client.server_thread import ServerThread


@pytest.fixture
def server_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    (tmp_path / "credentials.json").write_text('{"secret": "do-not-leak"}')
    (tmp_path / "subdir").mkdir()
    monkeypatch.chdir(tmp_path)
    thread = ServerThread()
    thread.start()
    yield thread
    if thread.is_alive():
        _request(thread, "POST", "/", json.dumps({"refresh_token": "shutdown"}))
    thread.join(timeout=5)


def _request(
    thread: ServerThread, method: str, path: str, body: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    conn = HTTPConnection("127.0.0.1", thread.server_port, timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    result = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
    conn.close()
    return result


FILE_METADATA_HEADERS = ("content-length", "last-modified", "content-type")


class TestWebRequestHandler:
    @pytest.mark.parametrize("method", ["HEAD", "GET"])
    def test_existing_and_missing_paths_are_indistinguishable(
        self, server_thread: ServerThread, method: str
    ) -> None:
        responses = [
            _request(server_thread, method, path)
            for path in ("/credentials.json", "/does-not-exist.json", "/subdir/", "/")
        ]
        statuses = {status for status, _, _ in responses}
        assert statuses == {200}
        for _, headers, body in responses:
            assert body == b""
            for header in FILE_METADATA_HEADERS:
                assert header not in headers
        assert server_thread.refresh_token is None

    def test_post_sets_refresh_token_and_stops_server(self, server_thread: ServerThread) -> None:
        status, _, _ = _request(server_thread, "POST", "/", json.dumps({"refresh_token": "abc"}))
        assert status == 200
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()
        assert server_thread.refresh_token == "abc"
