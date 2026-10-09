from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orders.handlers import handle_payment_captured, handle_payment_failed, handle_stock_rejected
from orders.models import Order, OrderStatus, ProcessedEvent
from orders.status import move_pending
from tests.conftest import event


def pending_order(session: Session, order_id: str = "o-1") -> Order:
    order = Order(
        id=order_id,
        status=OrderStatus.PENDING,
        status_reason=None,
        total_cents=5000,
        created_at=datetime.now(UTC),
    )
    session.add(order)
    session.commit()
    return order


def stock_rejected(order_id: str = "o-1") -> dict[str, object]:
    return event(
        "StockRejected", {"order_id": order_id, "sku": "BOOK-1", "reason": "insufficient stock"}
    )


def test_stock_rejected_rejects_the_order_with_a_reason(session: Session) -> None:
    order = pending_order(session)

    handle_stock_rejected(session, stock_rejected())

    assert order.status == OrderStatus.REJECTED
    assert order.status_reason == "insufficient stock: BOOK-1"


def test_payment_failed_rejects_the_order(session: Session) -> None:
    order = pending_order(session)

    handle_payment_failed(
        session, event("PaymentFailed", {"order_id": "o-1", "reason": "amount over limit"})
    )

    assert order.status == OrderStatus.REJECTED
    assert order.status_reason == "payment failed: amount over limit"


def test_a_redelivered_event_is_handled_once(session: Session) -> None:
    pending_order(session)
    envelope = stock_rejected()

    handle_stock_rejected(session, envelope)
    handle_stock_rejected(session, envelope)

    assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1


def test_the_first_rejection_reason_wins(session: Session) -> None:
    order = pending_order(session)

    handle_stock_rejected(session, stock_rejected())
    handle_payment_failed(session, event("PaymentFailed", {"order_id": "o-1", "reason": "late"}))

    assert order.status_reason == "insufficient stock: BOOK-1"


def test_an_event_for_an_unknown_order_is_recorded_and_ignored(session: Session) -> None:
    handle_stock_rejected(session, stock_rejected("missing"))

    assert session.get(Order, "missing") is None
    assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1


def payment_captured(order_id: str = "o-1") -> dict[str, object]:
    return event(
        "PaymentCaptured", {"order_id": order_id, "payment_id": "p-1", "amount_cents": 5000}
    )


def test_payment_captured_confirms_a_pending_order(session: Session) -> None:
    order = pending_order(session)

    handle_payment_captured(session, payment_captured())

    assert order.status == OrderStatus.CONFIRMED
    assert order.status_reason is None
    assert order.confirmed_at is not None


def test_a_redelivered_payment_captured_is_handled_once(session: Session) -> None:
    order = pending_order(session)
    envelope = payment_captured()

    handle_payment_captured(session, envelope)
    handle_payment_captured(session, envelope)

    assert order.status == OrderStatus.CONFIRMED
    assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1


def test_payment_captured_leaves_an_order_that_is_not_pending_alone(session: Session) -> None:
    order = pending_order(session)
    handle_stock_rejected(session, stock_rejected())

    handle_payment_captured(session, payment_captured())

    assert order.status == OrderStatus.REJECTED
    assert order.status_reason == "insufficient stock: BOOK-1"
    assert order.confirmed_at is None


def cancelled_order(session: Session, order_id: str = "o-1") -> Order:
    order = pending_order(session, order_id)
    order.status = OrderStatus.CANCELLED
    order.status_reason = "cancelled by customer"
    session.commit()
    return order


def payment_failed(order_id: str = "o-1") -> dict[str, object]:
    return event("PaymentFailed", {"order_id": order_id, "reason": "amount over limit"})


@pytest.mark.parametrize(
    ("handle", "make_event"),
    [
        pytest.param(handle_payment_captured, payment_captured, id="PaymentCaptured"),
        pytest.param(handle_stock_rejected, stock_rejected, id="StockRejected"),
        pytest.param(handle_payment_failed, payment_failed, id="PaymentFailed"),
    ],
)
def test_a_late_event_leaves_a_cancelled_order_alone(
    session: Session,
    handle: Callable[[Session, dict[str, Any]], None],
    make_event: Callable[[], dict[str, Any]],
) -> None:
    order = cancelled_order(session)
    envelope = make_event()

    handle(session, envelope)
    handle(session, envelope)

    assert order.status == OrderStatus.CANCELLED
    assert order.status_reason == "cancelled by customer"
    assert order.confirmed_at is None
    assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1


def test_payment_captured_does_not_overwrite_a_cancel_committed_after_the_order_was_read(
    session: Session,
) -> None:
    order = pending_order(session)
    assert order.status == OrderStatus.PENDING  # the worker's session now holds a PENDING copy
    with Session(session.get_bind()) as api_session:
        assert move_pending(
            api_session, "o-1", OrderStatus.CANCELLED, reason="cancelled by customer"
        )
        api_session.commit()

    handle_payment_captured(session, payment_captured())

    assert order.status == OrderStatus.CANCELLED
    assert order.confirmed_at is None
    with Session(session.get_bind()) as fresh:
        stored = fresh.get(Order, "o-1")
        assert stored is not None
        assert stored.status == OrderStatus.CANCELLED
