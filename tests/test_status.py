from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from orders.db import Base
from orders.models import Order, OrderStatus
from orders.status import move_order
from tests.test_handlers import pending_order


def test_a_pending_order_moves(session: Session) -> None:
    order = pending_order(session)

    assert move_order(session, "o-1", OrderStatus.REJECTED, reason="insufficient stock")
    session.commit()

    assert order.status == OrderStatus.REJECTED
    assert order.status_reason == "insufficient stock"


def test_extra_fields_are_set_with_the_move(session: Session) -> None:
    order = pending_order(session)
    confirmed_at = datetime(2026, 10, 9, 12, 0)

    assert move_order(session, "o-1", OrderStatus.CONFIRMED, confirmed_at=confirmed_at)
    session.commit()

    assert order.status == OrderStatus.CONFIRMED
    assert order.status_reason is None
    assert order.confirmed_at == confirmed_at


@pytest.mark.parametrize(
    "status", [OrderStatus.CONFIRMED, OrderStatus.REJECTED, OrderStatus.CANCELLED]
)
def test_an_order_that_is_not_pending_does_not_move(session: Session, status: OrderStatus) -> None:
    order = pending_order(session)
    order.status = status
    order.status_reason = "earlier"
    session.commit()

    assert not move_order(session, "o-1", OrderStatus.CANCELLED, reason="later")
    session.commit()

    assert order.status == status
    assert order.status_reason == "earlier"


def test_an_unknown_order_does_not_move(session: Session) -> None:
    assert not move_order(session, "missing", OrderStatus.CANCELLED)


@pytest.fixture
def file_engine(tmp_path: Path) -> Iterator[Engine]:
    """A database two sessions can share with real, separate transactions."""
    engine = create_engine(f"sqlite:///{tmp_path / 'orders.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_a_stale_copy_in_the_session_does_not_decide(file_engine: Engine) -> None:
    with Session(file_engine) as worker, Session(file_engine) as api:
        stale = pending_order(worker)
        assert stale.status == OrderStatus.PENDING  # the worker now holds a PENDING copy

        assert move_order(api, "o-1", OrderStatus.CANCELLED, reason="cancelled by customer")
        api.commit()

        assert not move_order(worker, "o-1", OrderStatus.CONFIRMED, confirmed_at=datetime.now(UTC))
        assert stale.status == OrderStatus.CANCELLED
        assert worker.get(Order, "o-1") is stale
        worker.commit()

    with Session(file_engine) as fresh:
        stored = fresh.get(Order, "o-1")
        assert stored is not None
        assert stored.status == OrderStatus.CANCELLED
        assert stored.status_reason == "cancelled by customer"
        assert stored.confirmed_at is None


def test_a_confirmed_order_moves_when_confirmed_is_allowed(session: Session) -> None:
    order = pending_order(session)
    order.status = OrderStatus.CONFIRMED
    session.commit()

    assert move_order(
        session,
        "o-1",
        OrderStatus.CANCELLED,
        from_statuses=(OrderStatus.PENDING, OrderStatus.CONFIRMED),
        reason="cancelled by customer",
    )
    session.commit()

    assert order.status == OrderStatus.CANCELLED
    assert order.status_reason == "cancelled by customer"


def test_a_rejected_order_does_not_move_even_when_confirmed_is_allowed(session: Session) -> None:
    order = pending_order(session)
    order.status = OrderStatus.REJECTED
    session.commit()

    assert not move_order(
        session,
        "o-1",
        OrderStatus.CANCELLED,
        from_statuses=(OrderStatus.PENDING, OrderStatus.CONFIRMED),
    )
    session.commit()

    assert order.status == OrderStatus.REJECTED
