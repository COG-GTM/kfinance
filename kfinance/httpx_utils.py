import asyncio
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
from queue import Queue
from typing import Any, Generator

import httpx
from httpx import Request, Response

from kfinance.client.fetch import KFinanceApiClient


# Context variable for tracking endpoint URLs across async contexts
_endpoint_tracker_queue: ContextVar[Queue[str] | None] = ContextVar(
    "endpoint_tracker_queue", default=None
)


def _origin(url: str | httpx.URL) -> tuple[bytes, bytes, int | None]:
    """Return the (scheme, host, port) origin of a url."""
    parsed = httpx.URL(url)
    return parsed.raw_scheme, parsed.raw_host, parsed.port


class KfinanceBearerAuth(httpx.Auth):
    def __init__(self, api_client: KFinanceApiClient) -> None:
        """"""
        self._api_client = api_client
        self._api_origin = _origin(api_client.api_host)

    def auth_flow(self, request: httpx.Request) -> Generator[Request, Response, None]:
        """Inject access token into auth header for requests to the kfinance api host."""
        if _origin(request.url) == self._api_origin:
            request.headers["Authorization"] = f"Bearer {self._api_client.access_token}"
        yield request


class KfinanceHttpxClient(httpx.AsyncClient):
    """httpx.AsyncClient subclass that automatically prefixes URLs with a base URL and includes endpoint tracking."""

    def __init__(self, api_client: KFinanceApiClient) -> None:
        """"""
        self._kfinance_base_url: str = f"{api_client.api_host}/api/v1"
        self._kfinance_origin = _origin(api_client.api_host)

        super().__init__(auth=KfinanceBearerAuth(api_client=api_client))

        # Auto-register cleanup on exit
        atexit.register(self._cleanup_on_exit)

    @contextmanager
    def endpoint_tracker(self) -> Generator[Queue[str], None, None]:
        """Context manager to track endpoint URLs accessed during execution.

        This is safe for concurrent async operations as it uses contextvars
        to ensure each async context gets its own tracking queue.

        Usage:
            with httpx_client.endpoint_tracker() as queue:
                # Make requests
                await httpx_client.get("some/endpoint")
                # After requests, dequeue URLs
                endpoint_urls = []
                while not queue.empty():
                    endpoint_urls.append(queue.get())
        """
        queue = Queue[str]()
        token = _endpoint_tracker_queue.set(queue)
        try:
            yield queue
        finally:
            _endpoint_tracker_queue.reset(token)

    def _cleanup_on_exit(self) -> None:
        """Clean up the httpx client on process exit."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.aclose())
            else:
                asyncio.run(self.aclose())
        except:  # noqa:E722
            pass  # Process is shutting down

    def _build_url(self, url: str) -> str:
        """Build the full URL by prepending base_url to relative URLs.

        Absolute URLs are only accepted if they point at the kfinance api host,
        so that requests (and the bearer token) never leave the trusted host.
        """
        if url.startswith(("http://", "https://")):
            if _origin(url) != self._kfinance_origin:
                raise ValueError(
                    f"Refusing to request {url!r}: absolute urls must point at {self._kfinance_base_url}"
                )
            return url
        return f"{self._kfinance_base_url}/{url.lstrip('/')}"

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
        """Override request to prepend base_url to relative URLs and track endpoints."""
        full_url = self._build_url(url)

        # Track endpoint if tracking is active in the current async context
        queue = _endpoint_tracker_queue.get(None)
        if queue is not None:
            queue.put(full_url)

        return await super().request(method=method, url=full_url, **kwargs)
