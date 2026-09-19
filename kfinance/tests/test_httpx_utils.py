import httpx
import pytest
from pytest_httpx import HTTPXMock

from kfinance.client.kfinance import Client
from kfinance.httpx_utils import KfinanceHttpxClient


@pytest.fixture
def kfinance_httpx_client(mock_client: Client) -> KfinanceHttpxClient:
    return KfinanceHttpxClient(api_client=mock_client.kfinance_api_client)


class TestBuildUrl:
    def test_relative_url_gets_base_url_prefix(
        self, kfinance_httpx_client: KfinanceHttpxClient
    ) -> None:
        assert (
            kfinance_httpx_client._build_url("/companies")  # noqa: SLF001
            == "https://kfinance.kensho.com/api/v1/companies"
        )

    def test_absolute_url_on_api_host_is_allowed(
        self, kfinance_httpx_client: KfinanceHttpxClient
    ) -> None:
        url = "https://kfinance.kensho.com/api/v1/companies"
        assert kfinance_httpx_client._build_url(url) == url  # noqa: SLF001

    def test_absolute_url_on_other_host_raises(
        self, kfinance_httpx_client: KfinanceHttpxClient
    ) -> None:
        with pytest.raises(ValueError, match="Refusing to request"):
            kfinance_httpx_client._build_url("https://evil.tld/companies")  # noqa: SLF001


class TestBearerAuth:
    @pytest.mark.asyncio
    async def test_token_added_for_api_host(
        self, kfinance_httpx_client: KfinanceHttpxClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url="https://kfinance.kensho.com/api/v1/companies", json={})
        await kfinance_httpx_client.get("/companies")
        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["Authorization"] == "Bearer foo"

    @pytest.mark.asyncio
    async def test_token_not_added_for_other_host(
        self, mock_client: Client, httpx_mock: HTTPXMock
    ) -> None:
        auth = KfinanceHttpxClient(api_client=mock_client.kfinance_api_client).auth
        httpx_mock.add_response(url="https://evil.tld/companies", json={})
        async with httpx.AsyncClient(auth=auth) as client:
            await client.get("https://evil.tld/companies")
        request = httpx_mock.get_request()
        assert request is not None
        assert "Authorization" not in request.headers
