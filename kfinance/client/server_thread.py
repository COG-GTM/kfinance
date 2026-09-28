from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from secrets import token_urlsafe
from threading import Thread
from urllib.parse import urlsplit


MAX_CONTENT_LENGTH = 64 * 1024


def origin_from_url(url: str) -> str:
    """Return the scheme://host[:port] origin of a url."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class ServerThread(Thread):
    def __init__(self, allowed_origin: str, daemon: bool = True) -> None:
        """Construct a thread to hold an HTTPServer and a custom HTTPRequestHandler.

        :param allowed_origin: The only browser origin allowed to post the refresh token.
        :type allowed_origin: str
        """
        Thread.__init__(self, daemon=daemon)
        self.refresh_token: str | None = None
        self.allowed_origin = allowed_origin.lower()
        self.state = token_urlsafe(32)
        handler = WebRequestHandler(self)
        # not actually binding on port 0, this will ask the kernel to bind to an unused port
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.server_port = self.server.server_port

    def run(self) -> None:
        """Run the server, but only until the refresh token is written to."""
        try:
            while self.refresh_token is None:
                self.server.handle_request()
        finally:
            self.server.server_close()


class WebRequestHandler(BaseHTTPRequestHandler):
    def __init__(self, thread: ServerThread):
        """Hold the thread itself in the handler, so the handler can set the refresh token."""
        self.thread = thread

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Instead of letting the server construct the handler, just make the handler callable."""
        super().__init__(*args, **kwargs)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is not None and compare_digest(
            origin.lower().encode(), self.thread.allowed_origin.encode()
        )

    def _respond(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def end_headers(self) -> None:
        """Add CORS headers for the expected login origin only."""
        if self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", self.thread.allowed_origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        """Answer the CORS preflight check, only for the expected login origin."""
        self._respond(204 if self._origin_allowed() else 403)

    def do_GET(self) -> None:
        """Never serve files or anything else."""
        self._respond(405)

    def do_HEAD(self) -> None:
        """Never serve files or anything else."""
        self._respond(405)

    def do_POST(self) -> None:
        """Receive the refresh token from the login webpage, which will shut off the server and the thread."""
        if not self._origin_allowed():
            self._respond(403)
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._respond(411)
            return
        if content_length <= 0:
            self._respond(400)
            return
        if content_length > MAX_CONTENT_LENGTH:
            self._respond(413)
            return

        try:
            post_dict = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400)
            return
        if not isinstance(post_dict, dict):
            self._respond(400)
            return

        state = post_dict.get("state")
        refresh_token = post_dict.get("refresh_token")
        if not isinstance(state, str) or not compare_digest(
            state.encode(), self.thread.state.encode()
        ):
            self._respond(403)
            return
        if not isinstance(refresh_token, str) or not refresh_token:
            self._respond(400)
            return

        self.thread.refresh_token = refresh_token
        self._respond(200)
