"""What orders-service does when an event arrives from another service."""

from typing import Any

from sqlalchemy.orm import Session

from orders.models import Order, OrderStatus, ProcessedEvent


def reject_order(session: Session, envelope: dict[str, Any], reason: str) -> None:
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    order = session.get(Order, envelope["data"]["order_id"])
    if order is not None and order.status == OrderStatus.PENDING:
        order.status = OrderStatus.REJECTED
        order.status_reason = reason
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
    order = session.get(Order, envelope["data"]["order_id"])
    if order is not None and order.status == OrderStatus.PENDING:
        order.status = OrderStatus.CONFIRMED
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()
