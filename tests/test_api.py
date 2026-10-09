import httpx2
import pytest
from fastapi.testclient import TestClient
from shop_contracts.messaging import InMemoryEventPublisher
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from orders.models import Order, OrderStatus
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


def place(client: TestClient) -> dict[str, object]:
    response = client.post("/orders", json=TWO_BOOKS_ONE_PEN)
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


def set_status_elsewhere(session: Session, order_id: str, status: str, reason: str | None) -> None:
    """Commit a status from a session of its own, as the worker would.

    The client's session keeps its PENDING copy of the order, so this also checks that
    the endpoint does not decide from a stale copy.
    """
    with Session(session.get_bind()) as worker:
        worker.execute(
            update(Order).where(Order.id == order_id).values(status=status, status_reason=reason)
        )
        worker.commit()


def test_cancelling_a_pending_order(client: TestClient) -> None:
    placed = place(client)

    response = client.post(f"/orders/{placed['id']}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["status_reason"] == "cancelled by customer"
    assert body["total_cents"] == placed["total_cents"]
    assert body["lines"] == placed["lines"]
    assert client.get(f"/orders/{placed['id']}").json() == body


def test_cancelling_twice_returns_the_same_order(client: TestClient) -> None:
    order_id = place(client)["id"]

    first = client.post(f"/orders/{order_id}/cancel")
    second = client.post(f"/orders/{order_id}/cancel")

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()


@pytest.mark.parametrize(
    ("status", "reason"),
    [(OrderStatus.CONFIRMED, None), (OrderStatus.REJECTED, "insufficient stock: BOOK-1")],
)
def test_cancelling_an_order_that_is_no_longer_pending_is_refused(
    client: TestClient, session: Session, status: OrderStatus, reason: str | None
) -> None:
    order_id = str(place(client)["id"])
    set_status_elsewhere(session, order_id, status, reason)

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == f"order is {status} and can no longer be cancelled"
    after = client.get(f"/orders/{order_id}").json()
    assert after["status"] == status
    assert after["status_reason"] == reason


def test_cancelling_a_missing_order_gives_404(client: TestClient) -> None:
    response = client.post("/orders/does-not-exist/cancel")

    assert response.status_code == 404
    assert response.json()["detail"] == "order not found"


def test_cancelling_publishes_nothing(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    order_id = place(client)["id"]

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert [envelope["type"] for envelope in publisher.published] == ["OrderPlaced"]


def test_cancelling_does_not_need_inventory(client: TestClient, catalog: FakeCatalog) -> None:
    order_id = place(client)["id"]
    catalog.error = httpx2.ConnectError("connection refused")
    catalog.requested.clear()

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert catalog.requested == []
