import httpx2
from fastapi.testclient import TestClient
from shop_contracts.messaging import InMemoryEventPublisher
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orders.models import Order
from tests.conftest import FakeCatalog

TWO_BOOKS_ONE_PEN = {"lines": [{"sku": "BOOK-1", "quantity": 2}, {"sku": "PEN-1", "quantity": 1}]}


def count_orders(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Order)) or 0


def test_placing_an_order_saves_it_as_pending_with_inventory_prices(client: TestClient) -> None:
    response = client.post("/orders", json=TWO_BOOKS_ONE_PEN)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["total_cents"] == 2 * 2500 + 300
    assert {line["sku"]: line["unit_price_cents"] for line in body["lines"]} == {
        "BOOK-1": 2500,
        "PEN-1": 300,
    }


def test_placing_an_order_publishes_order_placed(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    order_id = client.post("/orders", json=TWO_BOOKS_ONE_PEN).json()["id"]

    [envelope] = publisher.published
    assert envelope["type"] == "OrderPlaced"
    assert envelope["data"]["order_id"] == order_id
    assert envelope["data"]["total_cents"] == 5300
    assert len(envelope["data"]["lines"]) == 2


def test_unknown_sku_is_refused_and_nothing_is_saved_or_published(
    client: TestClient, session: Session, publisher: InMemoryEventPublisher
) -> None:
    response = client.post("/orders", json={"lines": [{"sku": "NOPE-1", "quantity": 1}]})

    assert response.status_code == 422
    assert "NOPE-1" in response.json()["detail"]
    assert count_orders(session) == 0
    assert publisher.published == []


def test_the_same_sku_twice_is_refused(client: TestClient) -> None:
    lines = [{"sku": "BOOK-1", "quantity": 1}, {"sku": "BOOK-1", "quantity": 2}]
    assert client.post("/orders", json={"lines": lines}).status_code == 422


def test_invalid_lines_are_refused_before_inventory_is_asked(
    client: TestClient, catalog: FakeCatalog
) -> None:
    for lines in (
        [],
        [{"sku": "BOOK-1", "quantity": 0}],
        [{"sku": "BOOK-1", "quantity": 101}],
        [{"sku": "../admin", "quantity": 1}],
    ):
        assert client.post("/orders", json={"lines": lines}).status_code == 422, lines

    assert catalog.requested == []


def test_inventory_down_gives_503_and_saves_nothing(
    client: TestClient, session: Session, catalog: FakeCatalog
) -> None:
    catalog.error = httpx2.ConnectError("connection refused")

    response = client.post("/orders", json=TWO_BOOKS_ONE_PEN)

    assert response.status_code == 503
    assert count_orders(session) == 0


def test_an_order_can_be_read_back(client: TestClient) -> None:
    order_id = client.post("/orders", json=TWO_BOOKS_ONE_PEN).json()["id"]

    response = client.get(f"/orders/{order_id}")

    assert response.status_code == 200
    assert response.json()["id"] == order_id


def test_reading_a_missing_order_gives_404(client: TestClient) -> None:
    assert client.get("/orders/does-not-exist").status_code == 404
