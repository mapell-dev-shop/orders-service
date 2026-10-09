from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from orders.db import Base


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    status_reason: Mapped[str | None] = mapped_column(String(200))
    total_cents: Mapped[int]
    created_at: Mapped[datetime]
    confirmed_at: Mapped[datetime | None]
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    quantity: Mapped[int]
    unit_price_cents: Mapped[int]
    order: Mapped[Order] = relationship(back_populates="lines")


class ProcessedEvent(Base):
    """Ids of events already handled, so a redelivered event changes nothing."""

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
