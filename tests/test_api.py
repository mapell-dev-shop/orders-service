from datetime import datetime
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from shop_contracts.messaging import EventNotDelivered, InMemoryEventPublisher
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from orders.api import app, get_publisher
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


def cancels(publisher: InMemoryEventPublisher) -> list[dict[str, Any]]:
    return [e["data"] for e in publisher.published if e["type"] == "OrderCancelled"]


def test_cancelling_a_rejected_order_is_refused(
    client: TestClient, session: Session, publisher: InMemoryEventPublisher
) -> None:
    order_id = str(place(client)["id"])
    set_status_elsewhere(session, order_id, OrderStatus.REJECTED, "insufficient stock: BOOK-1")

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "order is REJECTED and can no longer be cancelled"
    after = client.get(f"/orders/{order_id}").json()
    assert after["status"] == "REJECTED"
    assert after["status_reason"] == "insufficient stock: BOOK-1"
    assert cancels(publisher) == []


def test_cancelling_a_confirmed_order(
    client: TestClient, session: Session, publisher: InMemoryEventPublisher
) -> None:
    order_id = str(place(client)["id"])
    set_status_elsewhere(session, order_id, OrderStatus.CONFIRMED, None)

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["status_reason"] == "cancelled by customer"
    assert cancels(publisher) == [{"order_id": order_id}]


def test_cancelling_a_pending_order_tells_the_shop(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    order_id = place(client)["id"]

    client.post(f"/orders/{order_id}/cancel")

    assert cancels(publisher) == [{"order_id": order_id}]


def test_cancelling_again_tells_the_shop_again(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    order_id = place(client)["id"]

    client.post(f"/orders/{order_id}/cancel")
    client.post(f"/orders/{order_id}/cancel")

    assert cancels(publisher) == [{"order_id": order_id}, {"order_id": order_id}]


def test_cancelling_a_missing_order_tells_nobody(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    client.post("/orders/does-not-exist/cancel")

    assert cancels(publisher) == []


def test_cancelling_a_missing_order_gives_404(client: TestClient) -> None:
    response = client.post("/orders/does-not-exist/cancel")

    assert response.status_code == 404
    assert response.json()["detail"] == "order not found"


class RefusingPublisher:
    """Kafka refuses every event until refuse is set to False."""

    def __init__(self) -> None:
        self.refuse = True
        self.published: list[dict[str, Any]] = []

    def publish(self, envelope: dict[str, Any], key: str) -> None:
        if self.refuse:
            raise EventNotDelivered("Kafka did not confirm")
        self.published.append(envelope)


def test_a_cancel_whose_event_was_lost_is_repaired_by_cancelling_again(
    client: TestClient,
) -> None:
    order_id = place(client)["id"]
    kafka = RefusingPublisher()
    app.dependency_overrides[get_publisher] = lambda: kafka

    with pytest.raises(EventNotDelivered):
        client.post(f"/orders/{order_id}/cancel")
    assert client.get(f"/orders/{order_id}").json()["status"] == "CANCELLED"

    kafka.refuse = False
    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert [e["type"] for e in kafka.published] == ["OrderCancelled"]


def test_cancelling_does_not_need_inventory(client: TestClient, catalog: FakeCatalog) -> None:
    order_id = place(client)["id"]
    catalog.error = httpx2.ConnectError("connection refused")
    catalog.requested.clear()

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert catalog.requested == []


def test_an_order_shows_when_its_refund_came_back(client: TestClient, session: Session) -> None:
    order_id = str(place(client)["id"])
    assert client.get(f"/orders/{order_id}").json()["refunded_at"] is None

    client.post(f"/orders/{order_id}/cancel")
    with Session(session.get_bind()) as worker:
        worker.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(refunded_at=datetime(2026, 10, 10, 12, 0))
        )
        worker.commit()

    assert client.get(f"/orders/{order_id}").json()["refunded_at"] == "2026-10-10T12:00:00"
