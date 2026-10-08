"""Product prices, read from inventory-service over HTTP."""

from dataclasses import dataclass
from typing import Protocol

import httpx2


@dataclass(frozen=True)
class Product:
    sku: str
    price_cents: int


class ProductNotFound(Exception):
    pass


class ProductCatalog(Protocol):
    def get_product(self, sku: str) -> Product: ...


class HttpProductCatalog:
    def __init__(self, base_url: str) -> None:
        self._client = httpx2.Client(base_url=base_url, timeout=2.0)

    def get_product(self, sku: str) -> Product:
        response = self._client.get(f"/products/{sku}")
        if response.status_code == 404:
            raise ProductNotFound(sku)
        response.raise_for_status()
        body = response.json()
        return Product(sku=body["sku"], price_cents=body["price_cents"])
