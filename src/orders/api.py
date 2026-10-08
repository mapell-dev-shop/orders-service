import uuid
from datetime import UTC, datetime
from functools import cache
from typing import Annotated

import httpx2
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from shop_contracts.events import new_envelope
from shop_contracts.messaging import EventPublisher, KafkaEventPublisher
from sqlalchemy.orm import Session

from orders import settings
from orders.catalog import HttpProductCatalog, ProductCatalog, ProductNotFound
from orders.db import get_session
from orders.models import Order, OrderLine, OrderStatus

app = FastAPI(title="orders-service")


class LineIn(BaseModel):
    sku: str = Field(pattern=r"^[A-Z0-9-]{1,50}$")
    quantity: int = Field(ge=1, le=100)


class OrderIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=20)


class LineOut(BaseModel):
    sku: str
    quantity: int
    unit_price_cents: int


class OrderOut(BaseModel):
    id: str
    status: str
    status_reason: str | None
    total_cents: int
    lines: list[LineOut]


@cache
def get_catalog() -> ProductCatalog:
    return HttpProductCatalog(settings.INVENTORY_URL)


@cache
def get_publisher() -> EventPublisher:
    return KafkaEventPublisher(settings.KAFKA_BOOTSTRAP_SERVERS)


SessionDep = Annotated[Session, Depends(get_session)]


def to_out(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id,
        status=order.status,
        status_reason=order.status_reason,
        total_cents=order.total_cents,
        lines=[
            LineOut(sku=line.sku, quantity=line.quantity, unit_price_cents=line.unit_price_cents)
            for line in order.lines
        ],
    )


@app.post("/orders", status_code=201)
def place_order(
    order_in: OrderIn,
    session: SessionDep,
    catalog: Annotated[ProductCatalog, Depends(get_catalog)],
    publisher: Annotated[EventPublisher, Depends(get_publisher)],
) -> OrderOut:
    skus = [line.sku for line in order_in.lines]
    if len(set(skus)) != len(skus):
        raise HTTPException(422, "each sku may appear only once per order")
    try:
        prices = {sku: catalog.get_product(sku).price_cents for sku in skus}
    except ProductNotFound as exc:
        raise HTTPException(422, f"unknown sku: {exc}") from exc
    except httpx2.HTTPError as exc:
        raise HTTPException(503, "inventory-service is unavailable") from exc

    order = Order(
        id=str(uuid.uuid4()),
        status=OrderStatus.PENDING,
        status_reason=None,
        total_cents=sum(prices[line.sku] * line.quantity for line in order_in.lines),
        created_at=datetime.now(UTC),
        lines=[
            OrderLine(sku=line.sku, quantity=line.quantity, unit_price_cents=prices[line.sku])
            for line in order_in.lines
        ],
    )
    session.add(order)
    session.commit()
    publisher.publish(
        new_envelope(
            "OrderPlaced",
            {
                "order_id": order.id,
                "lines": [
                    {
                        "sku": line.sku,
                        "quantity": line.quantity,
                        "unit_price_cents": line.unit_price_cents,
                    }
                    for line in order.lines
                ],
                "total_cents": order.total_cents,
            },
        ),
        key=order.id,
    )
    return to_out(order)


@app.get("/orders/{order_id}")
def get_order(order_id: str, session: SessionDep) -> OrderOut:
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    return to_out(order)
