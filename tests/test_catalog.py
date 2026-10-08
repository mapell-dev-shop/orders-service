"""The real HTTP client for inventory-service, against a fake transport."""

from collections.abc import Callable

import httpx2
import pytest

from orders.catalog import HttpProductCatalog, Product, ProductNotFound

Handler = Callable[[httpx2.Request], httpx2.Response]


def catalog(handler: Handler) -> HttpProductCatalog:
    return HttpProductCatalog("http://inventory", transport=httpx2.MockTransport(handler))


def test_a_product_is_read_from_inventory() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/products/BOOK-1"
        body = {"sku": "BOOK-1", "name": "Clean Code", "price_cents": 2500, "stock": 10}
        return httpx2.Response(200, json=body)

    assert catalog(handler).get_product("BOOK-1") == Product("BOOK-1", 2500)


def test_404_from_inventory_means_an_unknown_sku() -> None:
    with pytest.raises(ProductNotFound):
        catalog(lambda request: httpx2.Response(404)).get_product("NOPE-1")


def test_a_server_error_from_inventory_is_an_http_error() -> None:
    with pytest.raises(httpx2.HTTPError):
        catalog(lambda request: httpx2.Response(500)).get_product("BOOK-1")


def test_no_connection_to_inventory_is_an_http_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(httpx2.HTTPError):
        catalog(handler).get_product("BOOK-1")
