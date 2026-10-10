"""What orders-service does when an event arrives from another service."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from orders.models import Order, OrderStatus, ProcessedEvent
from orders.status import move_order


def reject_order(session: Session, envelope: dict[str, Any], reason: str) -> None:
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    move_order(session, envelope["data"]["order_id"], OrderStatus.REJECTED, reason=reason)
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()


def handle_stock_rejected(session: Session, envelope: dict[str, Any]) -> None:
    data = envelope["data"]
    reject_order(session, envelope, f"{data['reason']}: {data['sku']}")


def handle_payment_failed(session: Session, envelope: dict[str, Any]) -> None:
    reject_order(session, envelope, f"payment failed: {envelope['data']['reason']}")


def handle_payment_captured(session: Session, envelope: dict[str, Any]) -> None:
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    move_order(
        session,
        envelope["data"]["order_id"],
        OrderStatus.CONFIRMED,
        confirmed_at=datetime.now(UTC),
    )
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()


def handle_payment_refunded(session: Session, envelope: dict[str, Any]) -> None:
    """Record when the money came back. The status stays as it is; the first refund time wins."""
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    session.execute(
        update(Order)
        .where(Order.id == envelope["data"]["order_id"], Order.refunded_at.is_(None))
        .values(refunded_at=datetime.now(UTC)),
        execution_options={"synchronize_session": False},
    )
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()
