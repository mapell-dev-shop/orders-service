from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shop_contracts.events import new_envelope, validate_event
from shop_contracts.messaging import InMemoryEventPublisher
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from orders.api import app, get_catalog, get_publisher
from orders.catalog import Product, ProductNotFound
from orders.db import Base, get_session


class FakeCatalog:
    def __init__(self, products: dict[str, Product]) -> None:
        self.products = products
        self.error: Exception | None = None
        self.requested: list[str] = []

    def get_product(self, sku: str) -> Product:
        self.requested.append(sku)
        if self.error is not None:
            raise self.error
        if sku not in self.products:
            raise ProductNotFound(sku)
        return self.products[sku]


def event(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """An envelope that passes the contract, as another service would publish it."""
    envelope = new_envelope(event_type, data)
    validate_event(envelope)
    return envelope


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        yield session


@pytest.fixture
def publisher() -> InMemoryEventPublisher:
    return InMemoryEventPublisher()


@pytest.fixture
def catalog() -> FakeCatalog:
    return FakeCatalog({"BOOK-1": Product("BOOK-1", 2500), "PEN-1": Product("PEN-1", 300)})


@pytest.fixture
def client(
    session: Session, publisher: InMemoryEventPublisher, catalog: FakeCatalog
) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_publisher] = lambda: publisher
    app.dependency_overrides[get_catalog] = lambda: catalog
    yield TestClient(app)
    app.dependency_overrides.clear()
