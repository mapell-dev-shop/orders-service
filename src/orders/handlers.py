"""What orders-service does when an event arrives from another service."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from orders.models import OrderStatus, ProcessedEvent
from orders.status import move_pending


def reject_order(session: Session, envelope: dict[str, Any], reason: str) -> None:
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    move_pending(session, envelope["data"]["order_id"], OrderStatus.REJECTED, reason=reason)
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
    move_pending(
        session,
        envelope["data"]["order_id"],
        OrderStatus.CONFIRMED,
        confirmed_at=datetime.now(UTC),
    )
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()
