from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from secrets import token_urlsafe
from threading import Thread
from time import monotonic
from urllib.parse import urlsplit


MAX_CONTENT_LENGTH = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 10
LOGIN_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 1


DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_from_url(url: str) -> str:
    """Return the scheme://host[:port] origin of a url, serialized the way browsers send it."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port is not None and parts.port != DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    return f"{scheme}://{host}"


class ServerThread(Thread):
    def __init__(
        self,
        allowed_origin: str,
        daemon: bool = True,
        login_timeout: float = LOGIN_TIMEOUT_SECONDS,
    ) -> None:
        """Construct a thread to hold an HTTPServer and a custom HTTPRequestHandler.

        :param allowed_origin: The only browser origin allowed to post the refresh token.
        :type allowed_origin: str
        :param login_timeout: Seconds to wait for the login before giving up.
        :type login_timeout: float
        """
        Thread.__init__(self, daemon=daemon)
        self.refresh_token: str | None = None
        self.allowed_origin = allowed_origin.lower()
        self.login_timeout = login_timeout
        # one-time value that the login page must echo back alongside the refresh token
        self.state = token_urlsafe(32)
        handler = WebRequestHandler(self)
        # not actually binding on port 0, this will ask the kernel to bind to an unused port
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        # handle_request returns after this many idle seconds so the login deadline is checked
        self.server.timeout = POLL_INTERVAL_SECONDS
        self.server_port = self.server.server_port

    def run(self) -> None:
        """Run the server until a valid refresh token is written to or the login times out."""
        deadline = monotonic() + self.login_timeout
        try:
            while self.refresh_token is None and monotonic() < deadline:
                self.server.handle_request()
        finally:
            self.server.server_close()


class WebRequestHandler(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT_SECONDS

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
        """Add CORS headers, but only for the expected login origin."""
        if self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", self.thread.allowed_origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Requested-With")
            self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        """Answer the CORS preflight check for the expected login origin only."""
        self._respond(204 if self._origin_allowed() else 403)

    def do_GET(self) -> None:
        """This should never come up, but don't serve files or anything regardless."""
        self._respond(200)

    def do_HEAD(self) -> None:
        """This should never come up, but don't serve files or anything regardless."""
        self._respond(200)

    def do_POST(self) -> None:
        """Receive the refresh token from the login webpage, which will shut off the server and the thread.

        The request must come from the expected login origin and carry the one-time state value
        that was put in the login URL. Anything else is rejected and the server keeps waiting.
        """
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
            post_data = self.rfile.read(content_length)
        except TimeoutError:
            # a client that stalls mid-body must not hold the single-threaded server
            self.close_connection = True
            return
        try:
            post_dict = json.loads(post_data)
        except ValueError:
            self._respond(400)
            return
        if not isinstance(post_dict, dict):
            self._respond(400)
            return

        state = post_dict.get("state")
        if not isinstance(state, str) or not compare_digest(
            state.encode(), self.thread.state.encode()
        ):
            self._respond(403)
            return

        refresh_token = post_dict.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            self._respond(400)
            return

        self.thread.refresh_token = refresh_token
        self._respond(200)
