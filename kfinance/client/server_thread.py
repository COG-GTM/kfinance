from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import secrets
from threading import Thread
from urllib.parse import urlparse


refresh_token: str | None = None

# The login page only posts a small JSON payload, so anything larger is rejected outright.
MAX_CONTENT_LENGTH = 8192


def origin_of(url: str) -> str:
    """Return the scheme://host[:port] origin of a url."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class ServerThread(Thread):
    def __init__(self, expected_origin: str, daemon: bool = True) -> None:
        """Construct a thread to hold an HTTPServer and a custon HTTPRequestHandler.

        :param expected_origin: the scheme://host[:port] of the login page allowed to post the
            refresh token. Requests from any other origin are rejected.
        :param daemon: whether the thread runs as a daemon thread
        """
        Thread.__init__(self, daemon=daemon)
        self.refresh_token = None
        self.expected_origin = expected_origin
        # The state binds the token post to this specific login attempt. It is sent to the login
        # page in the login url and has to be returned in the post body.
        self.state = secrets.token_urlsafe(32)
        handler = WebRequestHandler(self)
        # not actually binding on port 0, this will ask the kernel to bind to an unused port
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.server_port = self.server.server_port

    def run(self) -> None:
        """Run the server, but only until the refresh token is written to."""
        while self.refresh_token is None:
            self.server.handle_request()


class WebRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, thread: ServerThread):
        """Hold the thread itself in the handler, so the handler can set the refresh token."""
        self.thread = thread

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Instead of letting the server construct the handler, just make the handler callable."""
        super().__init__(*args, **kwargs)

    def request_origin_allowed(self) -> bool:
        """Whether the request comes from the login page origin.

        A request without an Origin or Referer header is not browser-initiated cross-site traffic,
        so it is allowed. Anything else has to match the expected login page origin.
        """
        origin = self.headers.get("Origin")
        if origin is not None:
            return origin == self.thread.expected_origin
        referer = self.headers.get("Referer")
        if referer is not None:
            return origin_of(referer) == self.thread.expected_origin
        return True

    def end_headers(self) -> None:
        """The headers you needs for a CORS check."""
        if self.headers.get("Origin") == self.thread.expected_origin:
            self.send_header("Access-Control-Allow-Origin", self.thread.expected_origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        return super(WebRequestHandler, self).end_headers()

    def do_OPTIONS(self) -> None:
        """OPTIONS is needed for a preflight check, apparently."""
        self.send_response(200 if self.request_origin_allowed() else 403)
        self.end_headers()

    def do_GET(self) -> None:
        """This should never come up, but don't serve files or anything regardless."""
        self.send_response(405)
        self.end_headers()

    def do_HEAD(self) -> None:
        """Don't let the file serving base class respond to HEAD requests."""
        self.send_response(405)
        self.end_headers()

    def do_POST(self) -> None:
        """Receive the refresh token from the client webpage, which will shut off the server and the thread.

        The post is only accepted when it comes from the login page origin and carries the state
        generated for this login attempt, so that another page cannot fixate a token on the client.
        """
        if not self.request_origin_allowed():
            self.send_response(403)
            self.end_headers()
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_response(411)
            self.end_headers()
            return
        if content_length < 0 or content_length > MAX_CONTENT_LENGTH:
            self.send_response(413)
            self.end_headers()
            return
        post_data = self.rfile.read(content_length)
        try:
            post_dict = json.loads(post_data)
            state = post_dict["state"]
            refresh_token = post_dict["refresh_token"]
        except (json.JSONDecodeError, TypeError, KeyError):
            self.send_response(400)
            self.end_headers()
            return
        if not isinstance(state, str) or not secrets.compare_digest(state, self.thread.state):
            self.send_response(403)
            self.end_headers()
            return
        if not isinstance(refresh_token, str) or not refresh_token:
            self.send_response(400)
            self.end_headers()
            return
        self.thread.refresh_token = refresh_token
        self.send_response(200)
        self.end_headers()
